"""All runtime settings for avtalsagent, read from environment variables.

What:
    One `Settings` class that lists every setting the system uses: the database
    address, the OpenAI key, which model plays which role and where data is
    downloaded from and stored.

Why:
    Settings in one typed place make it easy to see what the system depends on,
    and keep secrets out of the code. Model names live here so a model can be
    swapped without a code change (ADR 0005).

How:
    Pydantic Settings reads each field from an environment variable with the same
    name in upper case (`database_url` <- `DATABASE_URL`), or from a local `.env`
    file. Values are validated when `Settings()` is created, so a wrong value fails
    at start-up instead of in the middle of a run. Code gets the settings through
    `get_settings()`, which builds them once and reuses them.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # An empty value in .env (e.g. `OPENAI_API_KEY=`) counts as "not set".
        env_ignore_empty=True,
        extra="ignore",
    )

    # Matches the postgres service in docker-compose.yml with its default values.
    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+psycopg://avtalsagent:avtalsagent@localhost:5432/avtalsagent"
        ),
    )

    # SecretStr hides the value in repr() and logs. Optional until the first
    # milestone that calls OpenAI (M4), so the skeleton runs without a key.
    openai_api_key: SecretStr | None = None

    # Which model plays which role (ADR 0005).
    agent_model: str = "gpt-6.1-sol"
    reviewer_model: str = "gpt-6-astra"
    extraction_model: str = "gpt-6-luna"

    # Where the Excel master list "Alla giltiga ramavtal" is published (M1).
    register_excel_url: str = "https://www.avropa.se/giltigaramavtal/excel"
    # Downloaded files (Excel, PDFs). Not committed to git.
    data_dir: Path = Path("data")

    # Fetching agreement documents from avropa.se (M2).
    # The A-Ö index that links to every framework-agreement page.
    agreement_index_url: str = "https://www.avropa.se/ramavtal/ramavtal-a-o/"
    # Framework areas to fetch, named as in the register's "Ramavtalsområde".
    # Start small so the whole chain works before scaling up (plan, M2).
    fetch_areas: list[str] = [
        "IT-drift",
        "Bemanningstjänster",
        "IT-konsulttjänster Resurskonsulter",
        "Programvaror och tjänster",
    ]
    # File types to download. Docling (M3) reads both; price lists in xlsx are skipped.
    fetch_file_types: list[str] = ["pdf", "docx"]
    # Pause between requests, so the site is not loaded more than a person browsing.
    fetch_delay_seconds: float = 0.5

    # Checking the documents against the register (M4, step 5). Deviations a person has
    # looked at and accepted, each named by its key in the ingestion report; an accepted
    # deviation no longer holds the document back. The file is in git and reviewed like code.
    # A relative path is read from the working directory: the repository root, where the
    # commands are run. A missing file accepts nothing, and `process` logs a warning for it.
    accepted_findings_file: Path = Path("accepted_findings.toml")

    log_level: str = "INFO"

    @property
    def parsed_dir(self) -> Path:
        """Where step 2 stores each parsed file as JSON (M3)."""
        return self.data_dir / "parsed"

    @property
    def reports_dir(self) -> Path:
        """Where each run of steps 3-5 writes its ingestion report, markdown and JSON (M4)."""
        return self.data_dir / "reports"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the settings, built once per process."""
    return Settings()
