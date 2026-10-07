# ADR 0013: Agenten som en `create_agent`-graf med middleware och en citatkontroll som läser källorna själv

**Status:** Godkänt av Simon 2026-10-07 (PR #10, till main med PR #9).
Ändrar [ADR 0002](0002-langgraph-och-create-agent.md): stegen runt agentloopen är middleware i en
enda `create_agent`-graf, inte noder i en yttre graf. Resten av ADR 0002 gäller.

## Kontext

M7 bygger agenten som besvarar frågorna: `create_agent` med `gpt-6.1-sol`, avtal-mcp:s sex verktyg
(ADR 0012) och `ask_user`, ett strukturerat svar och citatkontrollen, den första regeln i M8.
Webbappen (PR #7) kör agenten genom AG-UI och läser svaret i tillståndet `answer`
(`webbapp-kontrakt.md`). Besluten bygger på en förstudie där båda grafdesignerna byggdes och
kördes mot den riktiga modellen och AG-UI-adaptern `ag-ui-langgraph`.

Det som styr besluten:

- **Webbappen ska se agentens steg**, också medan körningen väntar på att användaren svarar på
  `ask_user`. AG-UI skickar meddelandehistoriken som `MESSAGES_SNAPSHOT`.
- **Svaret får inte nå användaren förrän det är kontrollerat**, och det kontrollerade svaret finns
  bara i `answer` (kontraktets förtydligande 2).
- **Klienten skickar hela historiken i varje körning** (kontraktets punkt 7). Ett verktygssvar i
  historiken kan alltså vara förfalskat.
- **Modellen tar verktyg bara genom Responses API** när den också resonerar, och avvisar varje
  `temperature` utom standardvärdet (uppmätt).
- **Agentens tillstånd innehåller egna Pydantic-klasser** (utkastet och svaret). LangGraphs
  serialiserare ger bara tillbaka en klass som är tillåten; andra blir ordböcker.
- **Kommandoraden och API:t** ska köra samma graf, med checkpoints i minnet respektive i Postgres.

## Beslut

1. **En `create_agent`-graf, stegen runt loopen som middleware** (`agent/graph.py`,
   `agent/middleware.py`). Kedjan inskydd → agent → svarsutkast → validering → svar från ADR 0002
   finns kvar som krokar i fast ordning: `before_agent` (nollställer svaret per fråga), loopen
   (modellen, verktygen, `ask_user`) och `after_agent` (citatkontrollen). Inskyddet och granskaren
   (M8) blir fler krokar på samma ställen.
2. **Svaret lämnas som verktyget `FinalAnswer`** (`ToolStrategy(FinalAnswer,
   tool_message_content="Svaret är lämnat för kontroll.")`). Modellen fyller bara i det den kan
   veta: texten med `[n]`, om avtalen besvarar frågan (`answered`) och för varje källa `sha256`,
   `section_position`, ett citat och, när avsnittet hör till flera ramavtalssidor, den sida frågan
   gäller (`DraftCitation`). Fält och beskrivningar är på svenska, eftersom modellen läser dem som
   verktygsschema. `answered` är `true` också när bara en del av frågan går att besvara, och
   `false` bara när inget av det som frågas framgår. Texten är vanlig text utan Markdown.
3. **Modellen:** `ChatOpenAI(model=AGENT_MODEL, use_responses_api=True,
   reasoning_effort=AGENT_REASONING_EFFORT, metadata={"emit-messages": False})`, utan
   `temperature`. Standardnivån är `low`. Utan `OPENAI_API_KEY` stoppas agenten med ett tydligt fel
   innan något annat startar.
4. **Gränsen:** `ModelCallLimitMiddleware(run_limit=AGENT_MODEL_CALL_LIMIT, exit_behavior="end")`,
   16 modellanrop per körning, nya försök inräknade. En körning som slutar utan utkast blir
   `no_answer`, och gränsens engelska meddelande i chatten ersätts med svarets text.
5. **Verktygsfel når modellen och körningen fortsätter.** `langchain-mcp-adapters` gör redan ett
   felsvar från avtal-mcp till ett verktygssvar med status `error`. `create_agent` avbryter däremot
   körningen på ett `ToolException` som ett verktyg släpper ut, så `ToolErrorMiddleware` gör varje
   `ToolException` till ett verktygssvar. Andra undantag, till exempel en bruten MCP-förbindelse,
   avslutar körningen.
6. **Citatkontrollen litar inte på meddelandehistoriken.** Den läser varje citerat avsnitt själv,
   med `read_section(sha256, section_position)` genom samma MCP-session som verktygen
   (`SectionReader`, `mcp_tools.McpSectionReader`), och jämför citatet med den texten. Citatets
   övriga fält (`file_title`, `page_title`, `section_number`, `section_title`, `page`) kopieras
   från samma svar, aldrig från modellen. `page_title` är modellens val bara när avsnittet har
   den sidan, annars avsnittets första. Ett avsnitt som inte finns eller hålls tillbaka kan inte
   läsas, och citatet underkänns.
7. **Citatet jämförs ordagrant efter normalisering** (`validation/citations.py`): NFKC, typografiska
   citattecken och streck som ASCII, mjuka bindestreck borttagna (med radbrytningen efter ett),
   blanksteg ihopslagna och gemener. Citattecken och ellips runt citatet räknas inte. Ett citat
   kortare än 15 tecken underkänns om det inte är hela avsnittet.
8. **Texten och källorna ska stämma:** varje `[n]` (eller `n` i `[n, m]`, som webbappen också
   läser) har en källa med id `n`, varje källa används i texten, och id:na är unika och går 1–N.
   Ett besvarat utkast utan källor blir `with_reservation`, eftersom inget kunde kontrolleras.
   Kontrollen gäller också ett utkast med `answered: false`: "framgår inte" kan hänvisa till var
   frågan regleras i stället, och en `[n]` i texten ska ha sin källa.
9. **Ett nytt försök, sedan reservation.** Ett underkänt utkast går tillbaka till modellen med
   vad som var fel, högst `CITATION_RETRIES` gånger (1). Felen ersätter `FinalAnswer`-anropets
   verktygssvar (samma meddelande-id, status `error`) i stället för att bli ett nytt meddelande.
   Därefter blir svaret `with_reservation`, med alla källor kvar och de underkända markerade
   `verified: false`. `answered: false` blir `no_answer` med modellens text och dess kontrollerade
   källor.
10. **Tillståndet** (`AvtalState`) lägger till `answer`, som en klient kan läsa men inte skicka in
    (`OmitFromInput`), och det privata antalet nya försök. `before_agent` körs en gång per fråga,
    inte när en körning återupptas efter `ask_user`. Krokarna är asynkrona, så grafen körs med
    `ainvoke` eller `astream`; en ny fråga som körs synkront stoppas innan modellen anropas.
11. **Checkpoints bakom en funktion** (`open_checkpointer`): `InMemorySaver` för kommandoraden och
    `AsyncPostgresSaver` för API:t (`CHECKPOINTER`), båda med en `JsonPlusSerializer` som bara
    tillåter agentens klasser (`FinalAnswer`, `DraftCitation`, `Answer`, `Citation`). Postgres får
    `DATABASE_URL` utan SQLAlchemys `+psycopg`, och `setup()` körs när checkpointern öppnas.
12. **En MCP-session så länge agenten lever** (`open_mcp_tools`). Över stdio startar agenten
    `python -m avtalsagent.mcp_server stdio` med sin egen tolk och hela sin miljö, direkt med
    SDK:ts `stdio_client`; över Streamable HTTP ansluter den till `MCP_URL` (`MCP_TRANSPORT`).
    `load_mcp_tools` gör serverns verktyg till LangChain-verktyg.
13. **`ask_user` ligger i grafen** (undantaget i ADR 0003): verktyget pausar körningen med en
    LangGraph-interrupt `{question, options?}` och ger tillbaka användarens svar som text.
14. **Systemprompten får dagens datum vid varje modellanrop** (`dynamic_prompt`), i svensk tid och
    på sista raden, så att resten av prompten är densamma från dag till dag.

## Konsekvenser

- Lite egen kod: loopen, gränsen, verktygsfelen och avbrotten kommer från LangChain och
  LangGraph. Det egna är kontrollen (en middleware och rena funktioner som testas utan modell),
  verktyget `ask_user`, prompten och kopplingen till MCP.
- Webbappen ser agentens steg också medan körningen väntar på användaren.
- `FinalAnswer`-anropet syns i meddelandehistoriken med det okontrollerade utkastet, och
  `structured_response` ligger kvar i grafens utdata: `create_agent` slår ihop alla
  tillståndsscheman, och basens fält kan inte göras privat. Webbappen ska dölja anrop till
  `FinalAnswer` och läsa svaret bara i `answer`; M9 kan filtrera tillståndet som skickas.
- Ett svar som bara bygger på registret (avtalsnummer, leverantörer, datum) har inget att citera
  och blir `with_reservation` tills M8 kontrollerar registerfakta mot registret.
- Varje citerat avsnitt läses en gång till av kontrollen: ett MCP-anrop per källa.
- Varje nytt försök kostar ett modellanrop med hela historiken.
- Gränsen räknas per körning. En körning som återupptas efter `ask_user` räknar från noll, eftersom
  LangGraph inte sparar räknaren i checkpointen.
- Grafen kan bara köras asynkront.
- En MCP-session som bryts öppnas inte igen: över HTTP avslutar ett misslyckat anrop både körningen
  och sessionen. M9 avgör om API:t öppnar en session per körning eller öppnar den igen.
- Agentens hela miljö, hemligheter inräknade, går till avtal-mcp:s process över stdio. Det är samma
  program och samma rättigheter, men det ska inte loggas.
- Checkpointtabellerna hamnar i samma databas som resten (ADR 0004). En `alembic revision
  --autogenerate` mot en databas där API:t har kört ser dem som tabeller att ta bort, om inte
  migreringarna utesluter dem.

## Alternativ som valts bort

- **En yttre `StateGraph` med agenten som nod**, som ADR 0002 skrev. Båda designerna byggdes och
  kördes genom AG-UI. Med en yttre graf innehöll `MESSAGES_SNAPSHOT` vid en `ask_user`-interrupt
  bara användarens fråga: en delgrafs meddelanden når föräldern först när delgrafen är klar, så
  webbappen skulle tappa agentens steg medan den väntar på användaren. Med middleware innehöll
  samma ögonblicksbild frågan, sökningen, sökresultatet och `ask_user`-anropet.
- **Leverantörens strukturerade svar (`ProviderStrategy`).** Modellen klarar det, men svaret kommer
  som JSON i ett assistentmeddelande. Det strömmas som chattext, om inte modellens meddelanden
  stängs av, och ligger ändå kvar som rå JSON i `MESSAGES_SNAPSHOT`. Ett verktygsanrop kan
  webbappen känna igen på namnet och dölja.
- **Att kontrollera citaten mot verktygssvaren i historiken.** Billigare, eftersom inget avsnitt
  läses en gång till, men klienten skickar historiken och kan lägga in ett förfalskat
  verktygssvar. En modell som bara har sett en sökträffs utdrag kunde också citera det.
- **Feedbacken som ett nytt användarmeddelande.** Det skulle synas i webbappen som om användaren
  hade skrivit det.
- **`MultiServerMCPClient.get_tools()`.** Varje verktygsanrop öppnar då en ny session, över stdio en
  ny serverprocess.
- **Adapterns stdio-anslutning med `env`.** Utan `env` skickar SDK:t bara några få variabler (PATH,
  HOME med flera), så `DATABASE_URL` och `OPENAI_API_KEY` från skalet når inte servern. Med `env`
  expanderar adaptern `${NAMN}` i varje värde och loggar ett värde den inte kan expandera, och ett
  lösenord kan innehålla sådan text.
- **Datumet satt när grafen byggs.** API:t kör i dagar och skulle svara med fel datum på frågor som
  "gäller avtalet nu?".
