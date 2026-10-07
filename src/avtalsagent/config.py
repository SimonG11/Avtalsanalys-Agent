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
from typing import Literal
from urllib.parse import quote, unquote

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # An empty value in .env (e.g. `OPENAI_API_KEY=`) counts as "not set".
        env_ignore_empty=True,
        extra="ignore",
        # A value that fails validation is named by its field, not shown: DATABASE_URL holds
        # the database password.
        hide_input_in_errors=True,
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

    # The search (M5, step 6 and retrieval/; ADR 0011). The embedding model, shortened
    # by the API to `embedding_dimensions`. Changing either needs a new index (`index`).
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = Field(default=1536, gt=0)
    # Texts per embedding request; the API takes at most 2,048.
    embedding_batch_size: int = Field(default=100, gt=0, le=2048)
    # Chunks each branch of the hybrid search (vector and BM25) passes to the fusion.
    search_candidates: int = Field(default=100, gt=0)
    # The constant k of reciprocal rank fusion; 60 as in Cormack, Clarke and Büttcher (2009).
    rrf_k: int = Field(default=60, gt=0)
    # Sections a search returns when the caller sets no limit.
    search_limit: int = Field(default=8, gt=0)

    # The tool layer avtal-mcp (M6, mcp_server/; ADR 0012): where the agent reaches it, the
    # port it listens on, and the Host headers it accepts (DNS rebinding protection; "mcp:8001"
    # is its name in docker compose, the others are this machine).
    mcp_url: str = "http://localhost:8001/mcp"
    mcp_port: int = Field(default=8001, gt=0, lt=65536)
    mcp_allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "mcp:8001"]

    # The agent (M7, agent/; ADR 0013). How much the agent model reasons before each step;
    # the model takes these four levels and no temperature.
    agent_reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "low"
    # Model calls per run, new attempts included: a question, or its continuation after the
    # user has answered ask_user. A run that reaches the limit gets the status no_answer, so a
    # run that never settles has a bounded cost.
    agent_model_call_limit: int = Field(default=16, gt=0)
    # New attempts after a draft fails the answer check (M8: the citations, the register facts
    # or the review); after them the answer is given with reservation (with_reservation), with
    # notes on what could not be verified. Two, so a question gets at most three drafts.
    validation_retries: int = Field(default=2, ge=0)
    # How much the reviewer model (REVIEWER_MODEL) reasons before its verdict (M8, ADR 0015).
    reviewer_reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "low"
    # How the agent reaches avtal-mcp: "stdio" starts the server as its own child process
    # (the command line, local work), "streamable_http" connects to MCP_URL (the API's
    # container).
    mcp_transport: Literal["stdio", "streamable_http"] = "stdio"
    # Where a conversation's checkpoints are kept, which let a run pause for a question to
    # the user and go on: "memory" lasts as long as the process (the command line),
    # "postgres" is the database in DATABASE_URL (the API; ADR 0004).
    checkpointer: Literal["memory", "postgres"] = "memory"

    # The API (M9, api/; ADR 0014): the address and port it listens on. 127.0.0.1 keeps it on
    # this machine; the container listens on 0.0.0.0 and docker compose decides what is
    # published.
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, gt=0, lt=65536)

    # Tracing of the agent's runs in Langfuse (observability/; ADR 0021): off unless both keys
    # are set. The address is Langfuse Cloud's EU region; a self-hosted Langfuse has its own.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    log_level: str = "INFO"

    @property
    def parsed_dir(self) -> Path:
        """Where step 2 stores each parsed file as JSON (M3)."""
        return self.data_dir / "parsed"

    @property
    def reports_dir(self) -> Path:
        """Where each run of steps 3-5 writes its ingestion report, markdown and JSON (M4)."""
        return self.data_dir / "reports"

    def redact(self, text: str) -> str:
        """`text` without the OpenAI key, Langfuse's secret key or the database password.

        The password is replaced where an error would show it: in a database
        address (":password@", as written or percent-encoded) and in quotes, as
        libpq quotes a part of the address it cannot read. Alone it can be an
        ordinary word, as the default "avtalsagent" is, so it is not replaced
        everywhere.
        """
        for secret in (self.openai_api_key, self.langfuse_secret_key):
            if secret is not None and (key := secret.get_secret_value()):
                text = text.replace(key, "***")
        for host in self.database_url.hosts():
            if password := host.get("password"):
                # As written, as the server reads it and percent-encoded as libpq gets it.
                decoded = unquote(password)
                for form in {password, decoded, quote(decoded, safe="")}:
                    text = text.replace(f":{form}@", ":***@").replace(f'"{form}"', '"***"')
        return text


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the settings, built once per process."""
    return Settings()
