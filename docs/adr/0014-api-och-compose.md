# ADR 0014: API:t som ett tunt FastAPI-lager över agenten, en MCP-session per körning och hela systemet i Docker Compose

**Status:** Föreslaget (PR för M9).

## Kontext

M9 gör agenten (ADR 0013) nåbar för webbappen (ADR 0010, PR #7) och samlar hela systemet så att det
startar med ett kommando på en Mac med Apple silicon (M1 eller M2), där demot körs.
Webbappen pratar AG-UI med agenten, läser bara tillståndet `answer` och visar den citerade sidan i
en PDF-visare (`webbapp-kontrakt.md`). Besluten bygger på att `ag-ui-langgraph` 0.0.46 lästes i
källkoden och provkördes, i M7:s förstudie och igen i M9, mot den riktiga modellen.

Det som styr besluten:

- **Adapterns standardval passar inte kontraktet.** En `STATE_SNAPSHOT` innehåller hela tillståndet
  vid varje steg: alla meddelanden igen och det okontrollerade utkastet (`structured_response`).
  En misslyckad körning skickar undantagets text till webbläsaren. En `ask_user`-fråga skickas
  både som AG-UI:s standardutfall och som en äldre `CUSTOM`-händelse, och varje LangGraph-händelse
  dessutom som en `RAW`-kopia.
- **En MCP-session som bryts öppnas inte igen** (ADR 0013): över HTTP avslutar ett misslyckat anrop
  både körningen och sessionen. API:t körs i dagar.
- **Checkpointern i M7 använder en anslutning**, och en bruten anslutning öppnas inte igen.
- **Inläsningen behöver PyTorch och Doclings modeller**, och tolkningen av piloten tar ungefär två
  timmar på fyra kärnor. Agenten och verktygen behöver dem inte.
- **Demot körs på en Mac med arm64**, CI:s vanliga maskiner är amd64.

## Beslut

1. **API:t är ett tunt lager** (`src/avtalsagent/api/`): `POST /agui` kör agenten över AG-UI,
   `GET /api/documents/{sha256}/pdf` ger en citerad PDF, `GET /health` och `GET /agui/health`
   svarar utan databas. API:t har ingen egen affärslogik: grafen, verktygen, kontrollen och
   checkpointern är M6:s och M7:s.
2. **`AvtalAguiAgent`, adapterns `LangGraphAgent` med fyra ändringar** (`api/agui.py`):
   - en ögonblicksbild av tillståndet är bara `{"answer": ...}`, med `null` innan svaret är
     kontrollerat;
   - `RUN_ERROR` har en fast svensk text; undantaget loggas av adaptern, och API:ts loggformat tar
     bort OpenAI-nyckeln och databasens lösenord ur varje rad, tracebacks inräknade;
   - `emit_interrupt_outcome=True` och `enable_legacy_on_interrupt_event=False`: `ask_user` avslutar
     körningen med AG-UI:s standardutfall och ingen `CUSTOM`-händelse (webbappstråden bekräftade
     2026-10-07 att CopilotKit läser utfallet, och dess tester kör den formen ensam);
   - `emit_raw_events=False`.
3. **Routen är adapterns `add_langgraph_fastapi_endpoint`, utskriven**, eftersom agenten byggs per
   körning (beslut 4). Den finns i appen från början och syns i OpenAPI-beskrivningen.
4. **En MCP-session per körning** (`AgentRuns.open()`). Varje körning öppnar en session till
   avtal-mcp, bygger grafen på dess verktyg med den gemensamma modellen och checkpointern och
   stänger sessionen när strömmen är slut. avtal-mcp är tillståndslös, så en session kostar ett
   handslag och verktygslistan; samtalet ligger i checkpointern. Ett misslyckat anrop avslutar bara
   sin körning. Går sessionen inte att öppna blir körningen `RUN_STARTED` och `RUN_ERROR`; bryts den
   mitt i körningen avbryter MCP-klienten körningen utan att adaptern skickar något, och routen
   avslutar strömmen med `RUN_ERROR`. Ett verktygsanrop som körningen aldrig fick svar på visar
   grafen för modellen med ett felsvar (`AnswerOpenToolCalls`), annars vägrar OpenAI varje följande
   fråga i den tråden. Vid start öppnas en session en gång och stängs, så att en felaktig `MCP_URL`
   stoppar starten.
5. **Checkpointern får en anslutningspool** (`AsyncConnectionPool` med en anslutning, som
   kontrolleras innan den lånas ut och byts ut om Postgres har stängt den). Fler anslutningar
   ger inget: `AsyncPostgresSaver` gör en operation i taget bakom ett eget lås, så körningarna turas
   om på anslutningen för varje läsning och skrivning. LangGraphs tabeller ligger i samma databas (ADR 0004)
   men utesluts ur `alembic revision --autogenerate` (`db/migrations/env.py`).
6. **En ny fråga medan `ask_user` väntar** kör inte grafen: adaptern skickar den väntande frågan
   igen, och användaren svarar på den först. Webbappen visar då samma fråga. Ett avbrutet
   `ask_user` (AG-UI:s resume med status `cancelled`) når modellen som "Användaren svarade inte på
   frågan" i stället för adapterns markering.
7. **PDF-routen visar bara det verktygen visar**: en fil med en rad i `document_scope` som karantänen
   inte håller tillbaka, samma regel som avtal-mcp (`mcp_server.visibility`). Hashen måste vara 64
   gemena hexadecimala tecken. En Word-fil ger 404 med en förklaring, ett databasfel 503.
8. **En image för hela backend** (`Dockerfile`, `python:3.12-slim-bookworm` och uv 0.8.17,
   `uv sync --locked --no-dev`, en användare utan root). Samma image kör avtal-mcp, API:t och
   inläsningen med olika kommandon. PyTorch är CPU-bygget på både amd64 och arm64.
9. **Docker Compose med fem tjänster**: `postgres`, `mcp`, `api`, `web` och `ingest` (profilen
   `ingest`, startas för hand). Varje publicerad port är bunden till `127.0.0.1`. `.env` når
   containrarna via `env_file`, men adresserna inne i compose (`DATABASE_URL`, `MCP_URL`,
   `MCP_TRANSPORT=streamable_http`, `CHECKPOINTER=postgres`, `API_HOST`) sätts i compose-filen och
   vinner. `data/` monteras: läsbar för API:t (PDF:erna), skrivbar för inläsningen. Doclings
   modeller sparas i en egen volym.
10. **Demodatat lämnas som cache, inte som databas.** Tolkningsresultaten (`data/parsed`, 16 MB) och
    språkmodellens cache (`data/llm_cache`) från utvecklingsmiljön packas i en fil i projektets
    delade mapp, aldrig i git. Med dem på plats hoppar `ingest` över tolkningen för varje fil som
    har samma hash och parserversion; en fil som har ändrats på avropa.se tolkas igen.
11. **CI bygger och startar hela systemet på båda arkitekturerna** (`ubuntu-24.04` och
    `ubuntu-24.04-arm`): imagen, migreringarna, avtal-mcp, API:t och webbappen, och kontrollerar
    hälsokontrollerna, PDF-routens 404 direkt och genom webbappen och att checkpointtabellerna
    skapades. `up --wait` har en tidsgräns, i CI och i demots kommando, eftersom en tjänst som
    startas om gång på gång annars väntas på för evigt.

## Konsekvenser

- Webbappen får exakt kontraktets tillstånd, och det okontrollerade utkastet lämnar aldrig
  servern i en ögonblicksbild. Det finns kvar i `MESSAGES_SNAPSHOT` som `FinalAnswer`-anropet, som
  webbappen döljer (kontraktets punkt 13).
- En omstart av avtal-mcp syns bara som ett fel på frågan som pågick (provkört: med avtal-mcp nere
  kom `RUN_ERROR` på 0,1 s, och nästa fråga efter omstarten blev verifierad).
- Varje fråga kostar en ny MCP-session och en ny kompilering av grafen, några millisekunder lokalt.
  Över stdio, utan compose, startar varje fråga en ny serverprocess, vilket tar några sekunder.
- Användaren får ingen förklaring av ett fel i webbläsaren, bara att något gick fel; detaljerna
  finns i `docker compose logs api`.
- Den som känner till ett tråd-id kan fortsätta den tråden: API:t har ingen inloggning. Det är ett
  lokalt demo; portarna är bundna till `127.0.0.1`.
- Imagen är stor (PyTorch och Docling följer med också till avtal-mcp och API:t, som inte behöver
  dem). En image är enklare att bygga och förklara; en mindre image för API:t kan komma senare.
- Inläsningen i en container skriver i `./data` som användaren med uid 1000. På en Mac spelar det
  ingen roll; på Linux måste katalogen vara skrivbar för den användaren.

## Alternativ som valts bort

- **En MCP-session för hela processen**, som kommandoraden har. Enklare, men ett enda misslyckat
  anrop skulle bryta alla följande frågor tills API:t startades om.
- **Adapterns `add_langgraph_fastapi_endpoint` i livscykeln.** Fungerar (provkört i M7), men routen
  läggs då till medan appen startar, två gånger om livscykeln körs två gånger, och agenten byggs
  en gång för alla körningar.
- **Undantagets text i `RUN_ERROR`.** Mer hjälpsamt i webbläsaren, men texten kan innehålla en
  databasadress eller SQL.
- **Både `CUSTOM on_interrupt` och utfallet**, adapterns standard. Webbappen behöver bara utfallet,
  och två kanaler för samma fråga är en källa till dubbla frågor.
- **En färdig databasdump till demodatorn.** Kräver en Postgres i utvecklingsmiljön, som inte finns, och
  binder demot till ett läge i databasen. Cachen ger samma tidsvinst för tolkningen, och resten av
  inläsningen går fort.
- **Att köra API:t och avtal-mcp utan Docker på Macen.** Fungerar med `uv run`, men kräver att
  Python, uv och Postgres sätts upp för hand inför demot.
