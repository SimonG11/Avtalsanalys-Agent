# Avtalsanalys-Agent

En agent som besvarar frågor om Statens inköpscentrals ramavtal (avropa.se) där varje
påstående har en verifierad källa. Inläsningen av avtalen är ett fast workflow, och
frågebesvarandet är en agent i LangGraph som själv väljer verktyg och ordning.

> **Status:** M4 (extraktion, avstämning mot registret och inläsningsrapport). Projektet byggs en milstolpe i taget, M0–M12. Varje milstolpe
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
uv run python -m avtalsagent.ingestion run         # fetch, parse och process i ett svep
uv run python -m avtalsagent.ingestion outline     # visar hur varje dokument delades
uv run python -m avtalsagent.ingestion verify      # jämför avsnitten med PDF:ernas textlager
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

Kontroller som CI kör (pre-commit kör de tre första):

```bash
uv run ruff format --check   # formatering
uv run ruff check            # lint
uv run mypy                  # typkontroll (strict)
uv run pytest                # tester
```

CI startar dessutom Postgres med Docker Compose och kontrollerar att pgvector finns.

## Struktur

| Mapp | Innehåll |
|---|---|
| `src/avtalsagent/` | Koden, ett underpaket per lager (läggs till milstolpe för milstolpe) |
| `tests/unit/` | Tester utan databas eller LLM, speglar `src/` |
| `tests/integration/` | Tester mot riktig Postgres (testcontainers) |
| `tests/fixtures/` | Små exempelfiler, t.ex. riktiga rader ur Excel-registret |
| `docs/adr/` | Arkitekturbeslut, ett per fil |
| `docs/steg/` | Förklaring av varje milstolpe |
| `docker/` | Konfiguration för containrarna |

Kod, identifierare och kommentarer är på engelska. Dokumentationen är på svenska.
