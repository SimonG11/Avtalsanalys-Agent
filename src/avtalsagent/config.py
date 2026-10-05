"""All runtime settings for avtalsagent, read from environment variables.

What:
    One `Settings` class that lists every setting the system uses: the database
    address, the OpenAI key and which model plays which role.

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
        default=PostgresDsn("postgresql://avtalsagent:avtalsagent@localhost:5432/avtalsagent"),
    )

    # SecretStr hides the value in repr() and logs. Optional until the first
    # milestone that calls OpenAI (M4), so the skeleton runs without a key.
    openai_api_key: SecretStr | None = None

    # Which model plays which role (ADR 0005).
    agent_model: str = "gpt-6.1-sol"
    reviewer_model: str = "gpt-6-astra"
    extraction_model: str = "gpt-6-luna"

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the settings, built once per process."""
    return Settings()
