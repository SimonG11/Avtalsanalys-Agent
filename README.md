# Avtalsanalys-Agent

En agent som besvarar frågor om Statens inköpscentrals ramavtal (avropa.se) där varje
påstående har en verifierad källa. Inläsningen av avtalen är ett fast workflow, och
frågebesvarandet är en agent i LangGraph som själv väljer verktyg och ordning.

> **Status:** M7 (agenten med citatkontrollen). Projektet byggs en milstolpe i taget, M0–M12. Varje milstolpe
> förklaras i [`docs/steg/`](docs/steg/) och varje designbeslut i [`docs/adr/`](docs/adr/).

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
uv run python -m avtalsagent.mcp_server http       # verktygslagret avtal-mcp på http://127.0.0.1:8001/mcp
uv run python -m avtalsagent.mcp_server stdio      # samma verktyg över stdin och stdout
uv run python -m avtalsagent.agent "Hur stort är vitet i IT-drift Mindre?"   # frågar agenten
uv run python -m avtalsagent.agent                 # ett samtal: en fråga i taget, följdfrågor i samma tråd
uv run python -m avtalsagent.agent --json "…"      # svaret som JSON, som webbappen får det
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
terminalen. Svaret skrivs ut med status (Kontrollerat, Med reservation eller Inget svar) och
källorna, där ✓ betyder att citatet finns ordagrant i avsnittet.

Kontroller som CI kör (pre-commit kör de tre första):

```bash
uv run ruff format --check   # formatering
uv run ruff check            # lint
uv run mypy                  # typkontroll (strict)
uv run pytest                # tester
```

CI startar dessutom Postgres med Docker Compose och kontrollerar att pgvector finns.

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
| `evals/` | Testsamlingen (`evals/datasets/`) och mätningen av sökningen |
| `docs/adr/` | Arkitekturbeslut, ett per fil |
| `docs/steg/` | Förklaring av varje milstolpe |
| `docker/` | Konfiguration för containrarna |
| `web/` | Webbappen (Next.js och CopilotKit), se [`web/README.md`](web/README.md) |

Kod, identifierare och kommentarer är på engelska. Dokumentationen är på svenska.
