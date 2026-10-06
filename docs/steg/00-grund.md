# M0 – Grund

**Mål:** ett tomt men komplett skelett där kodkvalitet, tester, CI och databas fungerar innan
någon riktig logik skrivs. Allt som kommer senare byggs ovanpå det här och kontrolleras på samma sätt.

**Klart när:** `uv run pytest` och CI är gröna, och `docker compose up postgres` startar.

---

## Vad som byggdes, fil för fil

### 1. Paket och beroenden: `pyproject.toml`, `uv.lock`, `.python-version`

- **Vad:** beskriver Python-paketet `avtalsagent`, dess beroenden och inställningarna för alla verktyg.
- **Varför uv:** uv installerar rätt Python-version, skapar den virtuella miljön och låser exakta
  versioner av alla paket i `uv.lock`. Alla utvecklare och CI får då exakt samma miljö.
- **Hur:** `uv sync` läser `pyproject.toml` och `uv.lock` och installerar allt. Körbara beroenden
  (`pydantic`, `pydantic-settings`) ligger under `dependencies`. Utvecklingsverktygen (`ruff`, `mypy`,
  `pytest`, `pre-commit`) ligger i gruppen `dev` och följer inte med i en produktionsinstallation.
- Koden ligger under `src/` (src-layout). Testerna kör då mot det installerade paketet och inte mot
  filer som råkar ligga i arbetskatalogen.

### 2. Kodkvalitet: ruff och mypy

- **ruff** formaterar koden och letar fel (lint). Reglerna står i `[tool.ruff]` i `pyproject.toml`
  med en kommentar per regelgrupp. Regeln `D100`/`D104` kräver en docstring i varje modul, eftersom
  varje modul ska förklara *Vad*, *Varför* och *Hur*.
- **mypy --strict** kontrollerar typerna. Strict betyder att alla funktioner måste ha typade argument
  och returvärden. Pydantic-pluginet gör att mypy förstår Pydantic-modellerna.
- **Varför:** TokenTek granskar koden. Automatiska kontroller tar hand om stil och typfel, så att
  granskningen kan handla om logiken.

### 3. Kontroller före commit: `.pre-commit-config.yaml`

- **Vad:** kör ruff och mypy automatiskt vid varje `git commit` (aktiveras med `uv run pre-commit install`).
- **Hur:** krokarna anropar verktygen via `uv run`. Versionerna kommer då från `uv.lock`, så
  pre-commit och CI kan inte använda olika versioner av samma verktyg.

### 4. Inställningar: `src/avtalsagent/config.py`

- **Vad:** klassen `Settings` listar alla inställningar systemet använder: databasens adress,
  OpenAI-nyckeln och vilken modell som har vilken roll (ADR 0005).
- **Varför:** allt systemet är beroende av syns på ett ställe och är typat. Hemligheter finns aldrig i koden.
- **Hur:** Pydantic Settings läser varje fält från en miljövariabel med samma namn i versaler
  (`database_url` ← `DATABASE_URL`) eller från en lokal `.env`-fil. Värdena valideras när
  inställningarna skapas, så ett felaktigt värde stoppar programmet direkt vid start.
  - `openai_api_key` är en `SecretStr`, som visas som `**********` i loggar och utskrifter.
  - Den är valfri tills M4, där OpenAI används för första gången.
  - Tomma värden räknas som "inte satt" (`env_ignore_empty`), så en `.env` kopierad från
    `.env.example` fungerar direkt.
  - `get_settings()` bygger inställningarna en gång och återanvänder dem.

### 5. Miljövariabler: `.env.example` och `.gitignore`

- `.env.example` listar alla miljövariabler **utan värden**, med standardvärdet i en kommentar.
  Den kopieras till `.env`, som ligger i `.gitignore` och aldrig checkas in.
- `OPENAI_API_KEY` sätts lokalt i `.env` och i molnmiljön som miljövariabel.

### 6. Databas: `docker-compose.yml` och `docker/postgres/init.sql`

- **Vad:** startar PostgreSQL 17 med tillägget pgvector (ADR 0004).
- **Hur:**
  - Avbildningen är låst till en exakt version (`pgvector/pgvector:0.8.7-pg17-bookworm`).
  - Användare, lösenord och databas tas från `.env`, med `avtalsagent` som standard. Standardvärdena
    är bara för lokal utveckling.
  - `init.sql` körs en gång när databasen skapas och aktiverar pgvector (`CREATE EXTENSION vector`).
  - En `healthcheck` med `pg_isready` gör att `--wait` väntar tills databasen tar emot anslutningar.
  - Data sparas i en Docker-volym och överlever omstart. `docker compose down -v` tömmer den.
- Tabeller och migreringar (Alembic) kommer i M1.

```bash
docker compose up -d postgres --wait
docker compose exec postgres psql -U avtalsagent -c "\dx"   # visar att vector är installerat
```

### 7. Tester: `tests/`

- `tests/unit/` innehåller tester utan databas eller LLM och speglar `src/`.
  `test_config.py` kontrollerar att inställningarna:
  1. har standardvärden som stämmer med Docker Compose och ADR 0005,
  2. läses från miljövariabler,
  3. behandlar tomma värden som "inte satt",
  4. stoppar vid en ogiltig databasadress,
  5. aldrig visar OpenAI-nyckeln i `repr()` eller `model_dump()`,
  6. byggs bara en gång.
- `tests/integration/` (riktig Postgres via testcontainers) och `tests/fixtures/` fylls från M1.

### 8. CI: `.github/workflows/ci.yml`

Körs vid varje push och pull request. Tre jobb, vart och ett svarar på en fråga:

| Jobb | Fråga | Steg |
|---|---|---|
| `lint` | Följer koden stil- och typreglerna? | `ruff format --check`, `ruff check`, `mypy` |
| `test` | Går testerna igenom? | `pytest` |
| `compose` | Startar databasen, med pgvector? | `docker compose up -d postgres --wait` och en SQL-fråga som kontrollerar att `vector` är installerat |

`uv sync --locked` i CI stoppar om `uv.lock` inte stämmer med `pyproject.toml`, så ingen kan
lägga till ett beroende utan att låsa det.

### 9. Beslut och dokumentation: `docs/`

- `docs/adr/0001`–`0005` beskriver de fem grundbesluten: workflow för inläsning och agent för
  frågor, LangGraph med `create_agent`, MCP som verktygslager, Postgres som enda databas och
  OpenAI som modellleverantör. Varje ADR har kontext, beslut, konsekvenser och bortvalda alternativ.
- `docs/arkitektur.md` och `docs/validering.md` är de godkända dokumenten från fas 2 och 3.

---

## Medvetna val i M0

- **Inga tomma mappar för senare lager.** Planens struktur (`register/`, `ingestion/`, `agent/` osv.)
  skapas i den milstolpe som fyller den med kod. Då finns ingen kod i repot som inte gör något.
- **Bara två körbara beroenden.** LangGraph, MCP-SDK:t, SQLAlchemy och de andra läggs till i den
  milstolpe som använder dem, så att varje beroende kan motiveras i sin egen pull request.
- **Pydantic för data som sparas eller lämnar processen, frysta dataclasses inom processen.** M1–M3
  följer den konventionen. Det som sparas eller lämnar processen är Pydantic-modeller
  (`RegisterRow`, `AgreementPage`, `DocumentLink`, `ParsedDocument`, `Block`, `Section`, `Chunk`),
  så att värdena valideras och kan skrivas som JSON. Värden och stegresultat som stannar inom
  processen är frysta dataclasses (t.ex. `FetchResult`, `StoredDocument`, `ChunkedDocument`,
  `AgreementNumber`), som är enklare och inte kan ändras av misstag. Båda är typade och
  kontrolleras av `mypy --strict`.

## Så verifierar du M0 själv

```bash
uv sync
uv run ruff format --check && uv run ruff check && uv run mypy && uv run pytest
docker compose up -d postgres --wait
docker compose exec postgres psql -U avtalsagent -tAc "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
docker compose down -v
```
