# ADR 0012: avtal-mcp med MCP SDK:t 1.x, vanliga funktioner som verktyg och samma regel som indexet

**Status:** Godkänt av Simon 2026-10-07 (PR #9). Bygger på [ADR 0003](0003-mcp-som-verktygslager.md), som gäller.

## Kontext

ADR 0003 bestämde att agentens dataverktyg ligger i en egen MCP-server, `avtal-mcp`, som bara
får läsa. M6 bygger den. För MVP:n är det sex av planens åtta verktyg; `find_amendments` och
`calculate_date` kommer senare. Verktygen heter som i kontraktet med webbappen
(`webbapp-kontrakt.md`), på engelska: `search_documents`, `read_section`, `get_outline`,
`resolve_reference`, `list_documents` och `search_register`, i stället för planens
`sok_dokument`, `las_avsnitt` och så vidare. Argumenten heter som i kontraktet där dess punkt 5
har ett namn (`query`, `framework_area`, `agreement_number`, `sha256`, `section_number`,
`reference` och `limit`); `document_type`, `section_position`, `supplier`, `org_number`,
`valid_on` och `offset` är nya namn som ska skickas till webbappstråden.

Det som styr besluten:

- **Klienten bestämmer SDK-versionen.** Agenten (M7) laddar verktygen med
  `langchain-mcp-adapters`, vars senaste version (0.3.2) kräver `mcp<2`. Det fristående paketet
  `fastmcp` 4 kräver `mcp>=2`.
- **SDK:t 1.x skickar texten i varje undantag till modellen.** Ett databasfel kan innehålla SQL
  och anslutningens uppgifter.
- **SDK:t 1.x kör synkrona verktyg i sin händelseloop**, och SQLAlchemy-frågorna är synkrona.
- **Modellen ser bara verktygets beskrivning och argumentens JSON-schema.** Beskrivningarna
  måste synas där klienten läser dem, och felen måste säga vad modellen ska göra.
- **Karantänen får inte kringgås.** Sökindexet tar inte med det som steg 4 och 5 håller tillbaka
  (ADR 0011 beslut 1). Verktyg som läser ett avsnitt med sha256, visar en innehållsförteckning
  eller följer en hänvisning är andra vägar till samma text.
- **Svaret citerar avsnitt** med sha256, filens titel, avtalssidans titel, nummer, rubrik och
  sida (kontraktet). Verktygen ska ge de fälten, så att svarssteget kopierar och inte slår upp.
- **Servern ska köras i en egen container** och nås från agentens container som `mcp:8001`.

## Beslut

1. **Det officiella MCP SDK:t på 1.x-linjen,** `mcp>=1.30,<2`, med `FastMCP` från
   `mcp.server.fastmcp`, och `uvicorn>=0.54` för HTTP.
2. **Ett verktyg är en vanlig funktion** `verktyg(session, [embedder,] **argument) ->
   Pydantic-modell`, en per fil i `mcp_server/tools/`, med en svensk docstring som är dess
   beskrivning. `server._with_session` döljer `session` och `embedder` i schemat, öppnar en
   session per anrop och kör funktionen i en arbetstråd (`anyio.to_thread`). Verktygen
   registreras med `inspect.cleandoc` av docstringen, annoteringarna `readOnlyHint=True`,
   `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=False` och strukturerat svar.
   Varje verktyg ger en modell, aldrig en lista, så svaret har ett `outputSchema`.
3. **Felen är skrivna för modellen.** Ett verktyg signalerar med `NotFoundError`,
   `AmbiguousError`, `MissingArgumentError` eller `UnavailableError` (underklasser till SDK:ts
   `ToolError`, i `errors.py`) och ett svenskt meddelande som säger vad modellen ska göra
   härnäst. Alla andra undantag ersätts i `_with_session` med ett fast meddelande, ett för
   databasfel och ett för övriga fel, och detaljerna loggas. En sökning utan träffar är en tom
   lista, inte ett fel.
4. **Läsbehörighet i databasen:** serverns anslutningar sätter `default_transaction_read_only=on`,
   och varje transaktion har isoleringsnivån `REPEATABLE READ`, så ett anrop läser en
   ögonblicksbild (`create_db_engine(read_only=True)`). En egen databasroll med bara SELECT är en
   senare härdning.
5. **Samma regel som indexet.** En fil visas när den har en rad i `document_scope` (steg 6 skriver
   den bara för filer med indexerade bitar) och karantänen inte håller den; ett avsnitt när
   dessutom karantänen inte håller avsnittet. Regeln läses vid varje anrop (`visibility.py`).
   Avsnitt och hänvisningsmål som hålls tillbaka saknas i svaren och räknas (`held_back`,
   `held_back_targets`); att läsa ett ger `NotFoundError` som säger att det hålls tillbaka.
   Mellan `process` och `index` visar verktygen ingenting, och `search_documents` och
   `list_documents` säger med `UnavailableError` att indexet inte är byggt, i stället för att ge
   en tom lista.
6. **Filtren kontrolleras mot registret.** Ett ramavtalsområde och ett avtalsnummer görs om till
   registrets stavning (området utan hänsyn till versaler, numret med `agreement_key`). Har
   registret flera stavningar av ett avtal med samma nyckel, ger `search_register` raderna för
   alla, och dokumentverktygen använder den som steg 6 sparade i `document_scope`. Ett nummer
   utan leverantörens löpnummer som registret har som upphandling (`procurement_key`) betyder hela
   upphandlingen, i alla tre verktyg som tar `agreement_number`. Ett okänt värde är ett fel, inte
   en tom lista som modellen skulle tolka som att avtalen inte säger något. `list_documents`
   använder sökningens egna villkor (`hybrid_search.scope_conditions`), så filtren betyder samma
   sak i båda.
7. **Argumenten är delade `Annotated`-typer** i `arguments.py`, med gränser och svenska
   beskrivningar. Namnen är kontraktets där punkt 5 har ett; `document_type`,
   `section_position`, `supplier`, `org_number`, `valid_on` och `offset` är nya och skickas till
   webbappstråden. Servern kontrollerar gränserna, och att ingen text har tecknet NUL, innan en
   session öppnas. Ett argumentnamn som verktyget inte har ger ett fel som räknar upp
   verktygets argument, och varje schema har `additionalProperties: false` (`server.AvtalMCP`):
   SDK:t skulle annars släppa argumentet, och ett felstavat filter ge ett svar utan filter. Ett
   valfritt arguments typ har `None` i sig, så att beskrivningen står på argumentet och inte inuti
   `anyOf`; dokumenttyperna står som strängar i schemat, utan enumens engelska docstring.
8. **Varje avsnitt i ett svar har citatfälten** (`results.SectionRef`): `sha256`, `file_title`,
   `document_type`, `page_titles`, `section_position`, `section_number`, `section_title`,
   `page_start` och `page_end`. `read_section` ger avsnittets text exakt som den är sparad,
   eftersom citatkontrollen jämför citaten med den. Ett avsnitt kan anges med nummer, med plats
   eller med båda, och då måste de gälla samma avsnitt.
9. **Transporter:** Streamable HTTP på `/mcp`, port 8001, tillståndslöst (`stateless_http=True`)
   och med JSON-svar (`json_response=True`), eftersom inget verktyg strömmar; stdio för lokala
   klienter. `/health` svarar utan att fråga databasen. Skyddet mot DNS rebinding är på: bara
   Host-huvudena i `MCP_ALLOWED_HOSTS` (standard `localhost:*`, `127.0.0.1:*`, `mcp:8001`) och
   Origin `http://localhost:*` och `http://127.0.0.1:*` tas emot.
10. **Utan `OPENAI_API_KEY` startar servern ändå.** `search_documents` svarar då med ett fel som
    pekar på `list_documents` och `get_outline`, och de andra verktygen fungerar. Med nyckel
    bäddas frågan in med 15 sekunders timeout och ett nytt försök, eftersom anropet håller en
    databasanslutning och en arbetstråd; `index` behåller OpenAI-klientens standard.
11. **Tester utan språkmodell:** enhetstester av schemat, annoteringarna, argumentens gränser,
    felen och HTTP-kontrollerna utan databas, med SDK:ts klient i minnet
    (`mcp.shared.memory.create_connected_server_and_client_session`); integrationstester mot
    Postgres som kör varje verktyg på M5:s korpus genom en skrivskyddad anslutning, med ett anrop
    per verktyg genom samma klient.

## Konsekvenser

- Agenten kan bara läsa, och bara det som inläsningen släpper igenom. Regeln är densamma som
  sökindexets, så det finns en regel att förklara och testa.
- Varje verktyg är en funktion som kan läsas och testas för sig, utan MCP och utan språkmodell.
- Svarssteget i M7 kopierar citatfälten från verktygens svar.
- SDK:t 1.x skriver "Error executing tool <namn>: " på engelska före varje felmeddelande. Det går
  inte att ändra utan att ersätta SDK:ts felhantering.
- Varje anrop läser `document_scope` och karantänen. Det är en liten fråga vid urvalets storlek.
- `default_transaction_read_only` kan stängas av av en klient som skickar egen SQL. Verktygen gör
  inte det, men det säkra skyddet är en databasroll med bara SELECT.
- Två nya beroenden, `mcp` och `uvicorn`, och deras (bland andra `starlette`).
- När agentens klient stöder `mcp` 2 kan servern flyttas dit. Det ändrar `server.py`, inte
  verktygsfunktionerna, som bara når SDK:t genom felklasserna i `errors.py`.
- Testerna använder `mcp.shared.memory`, som enligt en kommentar i SDK:t ska få en riktig
  klient i stället.

## Alternativ som valts bort

- **`mcp` 2.x eller `fastmcp` 4.** Nyare, men `langchain-mcp-adapters` 0.3.2, agentens klient,
  kräver `mcp<2`, och `fastmcp` 4 kräver `mcp` 2.
- **Servern monterad i API:t.** Det går, men API:t måste då köra MCP-sessionernas livscykel,
  och ADR 0003 vill ha en egen tjänst med en tydlig gräns. En egen container kan också få en egen
  databasroll.
- **stdio i testerna**, som ADR 0003 skrev. stdio startar en Python-process per klientsession.
  SDK:ts klient i minnet kör servern i testets process utan port och utan process, och testar
  samma schema, annoteringar och svar.
- **Verktygen som funktioner i agenten.** Valdes bort redan i ADR 0003: då finns ingen gräns
  mellan agenten och datan.
- **Ett fel när en sökning inte hittar något.** Modellen skulle tolka det som ett anrop som gick
  fel och försöka igen; en tom lista säger att det inte finns något.
