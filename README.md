# Avtalsanalys-Agent

En agent som besvarar frågor om Statens inköpscentrals ramavtal (avropa.se) där varje
påstående har en verifierad källa. Inläsningen av avtalen är ett fast workflow, och
frågebesvarandet är en agent i LangGraph som själv väljer verktyg och ordning.

> **Status:** Den förenklade M11 (mätningen av agentens svar på testsamlingen), efter M0–M10. Projektet byggs en milstolpe i taget, M0–M12. Varje milstolpe
> förklaras i [`docs/steg/`](docs/steg/) och varje designbeslut i [`docs/adr/`](docs/adr/).

## Kör demot

Du behöver Docker Desktop och en OpenAI-nyckel. Det fungerar likadant på en Mac med Apple silicon
som på en dator med Intel eller AMD.

```bash
cp .env.example .env                               # sätt OPENAI_API_KEY i .env
docker compose --profile ingest run --rm ingest    # första gången: registret och dokumenten
docker compose up -d --wait --wait-timeout 300     # databasen, avtal-mcp, API:t och webbappen
```

Öppna sedan http://localhost:3000. Slutar kommandot med att en tjänst inte blev frisk, visar
`docker compose logs api` varför (till exempel att `OPENAI_API_KEY` saknas). Första inläsningen tolkar alla dokument med Docling, vilket tar
ungefär två timmar; med en sparad tolkningscache i `data/` hoppar den över tolkningen.
[Steg 09](docs/steg/09-api.md) förklarar varje steg, cachen och hur du felsöker.

När allt är igång mäter `docker compose --profile eval run --rm eval` agentens svar på de 30
testfrågorna: rätt enligt facit, kontrollerade citat, tid och kostnad. Rapporten hamnar i
`evals/reports/` ([steg 11](docs/steg/11-utvardering.md)).

## Kom igång

Du behöver [uv](https://docs.astral.sh/uv/) och Docker.

```bash
uv sync                               # installerar Python 3.12-miljön och alla verktyg
cp .env.example .env                  # fyll i det du behöver, t.ex. OPENAI_API_KEY
uv run pre-commit install             # kör ruff format, ruff check och mypy före varje commit

docker compose up -d postgres --wait  # startar Postgres 17 med pgvector
uv run alembic upgrade head           # skapar tabellerna
uv run pytest                         # kör testerna (integrationstesterna kräver Docker)

uv run python -m avtalsagent.register --download   # hämtar och läser in Excel-registret
uv run python -m avtalsagent.ingestion fetch       # hämtar avtalsdokumenten för urvalet
uv run python -m avtalsagent.ingestion parse       # tolkar dokumenten med Docling
uv run python -m avtalsagent.ingestion process     # avsnitt, metadata, kontroll mot registret, rapport
uv run python -m avtalsagent.ingestion index       # bygger sökindexet (embeddings och BM25)
uv run python -m avtalsagent.ingestion run         # fetch, parse, process och index i ett svep
uv run python -m avtalsagent.ingestion search "Hur stort är vitet?"   # provar sökningen
uv run python -m avtalsagent.ingestion outline     # visar hur varje dokument delades
uv run python -m avtalsagent.ingestion verify      # jämför avsnitten med PDF:ernas textlager
uv run python -m evals.run_retrieval_eval          # mäter sökningen på testsamlingen
uv run python -m evals.run_answer_eval             # mäter agentens svar på testsamlingen (M11)
uv run python -m avtalsagent.mcp_server http       # verktygslagret avtal-mcp på http://127.0.0.1:8001/mcp
uv run python -m avtalsagent.mcp_server stdio      # samma verktyg över stdin och stdout
uv run python -m avtalsagent.agent "Hur stort är vitet i IT-drift Mindre?"   # frågar agenten
uv run python -m avtalsagent.agent                 # ett samtal: en fråga i taget, följdfrågor i samma tråd
uv run python -m avtalsagent.agent --json "…"      # svaret som JSON, som webbappen får det
uv run python -m avtalsagent.api                   # API:t på http://127.0.0.1:8000 (POST /agui, PDF:erna)
```

Första gången tar `parse` för urvalet ungefär två timmar på fyra processorkärnor. `uv sync`
installerar PyTorch för processorn från `download.pytorch.org`, och den första tolkningen laddar ner
Doclings modeller från Hugging Face, så miljön måste nå `download.pytorch.org`,
`download-r2.pytorch.org` och `*.hf.co`. Senare körningar återanvänder det sparade resultatet för
varje fil som samma parserversion redan har tolkat.

`process` skriver inläsningsrapporten till `data/reports/` (markdown och JSON). Ett dokument som
avviker från registret hålls i karantän; en avvikelse som en person har granskat och godkänt skrivs
in i `accepted_findings.toml` i repots rot. Språkmodellen används bara när `OPENAI_API_KEY` är satt,
och `fetch`, `process` och `run` tar `--area` för att välja andra ramavtalsområden än inställningen.
`process` läser ändå alla hämtade filer; områdena avgör bara vilka avtal täckningen gäller.

`index`, `run`, `search` och mätningen behöver `OPENAI_API_KEY` för embeddings. `process` tömmer
sökindexet, så kör `index` efter den; embeddings som redan finns hämtas ur en cache i databasen.

`avtal-mcp` ([M6](docs/steg/06-verktyg.md)) läser databasen utan att kunna skriva och visar bara det
som sökindexet visar. Utan `OPENAI_API_KEY` svarar `search_documents` med ett fel och de andra
verktygen fungerar. `/health` svarar utan databas.

Agenten ([M7](docs/steg/07-agent.md)) behöver `OPENAI_API_KEY` och ett byggt sökindex (`index`).
Den startar avtal-mcp själv över stdio (`MCP_TRANSPORT=streamable_http` ansluter i stället till
`MCP_URL`), visar varje verktygsanrop medan den arbetar och ställer sina frågor till dig i
terminalen. Svaret skrivs ut med status (Kontrollerat, Med reservation eller Inget svar), det som
inte kunde kontrolleras, källorna, där ✓ betyder att citatet finns ordagrant i avsnittet, och
registerraderna som svarets uppgifter ur registret stämmer med. Innan svaret visas kontrolleras
citaten och registeruppgifterna, och en andra modell (`gpt-6-astra`) granskar att källorna stöder
svaret ([M8](docs/steg/08-validering.md)); ett underkänt svar går tillbaka till agenten högst två
gånger.

API:t ([M9](docs/steg/09-api.md)) kör samma agent för webbappen över AG-UI (`POST /agui`) och ger
PDF:en som ett citat pekar på (`GET /api/documents/{sha256}/pdf`). Det behöver `OPENAI_API_KEY`
och avtal-mcp; i Docker Compose ansluter det till avtal-mcp över HTTP och sparar samtalen i Postgres.

Kontroller som CI kör (pre-commit kör de tre första):

```bash
uv run ruff format --check   # formatering
uv run ruff check            # lint
uv run mypy                  # typkontroll (strict)
uv run pytest                # tester
```

CI bygger dessutom backendens image och startar Postgres, avtal-mcp och API:t med Docker Compose,
på både amd64 och arm64, och kontrollerar att pgvector finns och att API:t svarar.

Webbappen har egna kommandon och kan provas mot en mock av agenten, utan backend:

```bash
docker compose -f web/compose.mock.yaml up --build   # sedan http://localhost:3000
```

Se [`web/README.md`](web/README.md).

## Struktur

| Mapp | Innehåll |
|---|---|
| `src/avtalsagent/` | Koden, ett underpaket per lager (läggs till milstolpe för milstolpe) |
| `tests/unit/` | Tester utan databas eller LLM, speglar `src/` |
| `tests/integration/` | Tester mot riktig Postgres (testcontainers) |
| `tests/fixtures/` | Små exempelfiler, t.ex. riktiga rader ur Excel-registret |
| `evals/` | Testsamlingen (`evals/datasets/`), mätningen av sökningen och av agentens svar |
| `docs/adr/` | Arkitekturbeslut, ett per fil |
| `docs/steg/` | Förklaring av varje milstolpe |
| `docker/` | Konfiguration för containrarna |
| `web/` | Webbappen (Next.js och CopilotKit), se [`web/README.md`](web/README.md) |

Kod, identifierare och kommentarer är på engelska. Dokumentationen är på svenska.
