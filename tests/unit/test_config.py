"""Tests for avtalsagent.config.

What:
    Checks that settings have working defaults, are read from environment
    variables, reject invalid values and never show the OpenAI key.

Why:
    Every later milestone reads its settings from here, and the key is a secret
    that must never end up in logs.

How:
    Each test sets environment variables with pytest's `monkeypatch` and builds
    `Settings(_env_file=None)` so a developer's local .env cannot affect the result.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from avtalsagent.config import Settings, get_settings

ENV_VARS = (
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "AGENT_MODEL",
    "REVIEWER_MODEL",
    "EXTRACTION_MODEL",
    "LOG_LEVEL",
    "REGISTER_EXCEL_URL",
    "DATA_DIR",
    "AGREEMENT_INDEX_URL",
    "FETCH_AREAS",
    "FETCH_FILE_TYPES",
    "FETCH_DELAY_SECONDS",
    "ACCEPTED_FINDINGS_FILE",
    "AGENT_REASONING_EFFORT",
    "AGENT_MODEL_CALL_LIMIT",
    "CITATION_RETRIES",
    "MCP_TRANSPORT",
    "CHECKPOINTER",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_docker_compose_and_adr_0005() -> None:
    settings = Settings(_env_file=None)

    assert str(settings.database_url) == (
        "postgresql+psycopg://avtalsagent:avtalsagent@localhost:5432/avtalsagent"
    )
    assert settings.openai_api_key is None
    assert settings.agent_model == "gpt-6.1-sol"
    assert settings.reviewer_model == "gpt-6-astra"
    assert settings.extraction_model == "gpt-6-luna"


def test_values_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/other")
    monkeypatch.setenv("AGENT_MODEL", "gpt-6-astra")

    settings = Settings(_env_file=None)

    assert str(settings.database_url) == "postgresql://u:p@db:5432/other"
    assert settings.agent_model == "gpt-6-astra"


def test_fetch_areas_are_read_as_a_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_AREAS", '["IT-drift", "Möbler och inredning"]')

    assert Settings(_env_file=None).fetch_areas == ["IT-drift", "Möbler och inredning"]


def test_empty_value_counts_as_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert Settings(_env_file=None).openai_api_key is None


def test_invalid_database_url_fails_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-url")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_an_invalid_database_url_is_named_without_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://agent:Hemligt-Losen1@db:5x/a")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert "database_url" in str(error.value)
    assert "Hemligt-Losen1" not in str(error.value)


def test_openai_key_is_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-not-a-real-key"
    assert "sk-test-not-a-real-key" not in repr(settings)
    assert "sk-test-not-a-real-key" not in str(settings.model_dump())


def test_accepted_findings_are_read_relative_to_the_working_directory_by_default() -> None:
    # The commands are run from the repository root, where the file is.
    path = Settings(_env_file=None).accepted_findings_file
    assert path == Path("accepted_findings.toml")
    assert not path.is_absolute()


def test_accepted_findings_file_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCEPTED_FINDINGS_FILE", "/srv/avtalsagent/accepted_findings.toml")

    settings = Settings(_env_file=None)

    assert settings.accepted_findings_file == Path("/srv/avtalsagent/accepted_findings.toml")


def test_reports_are_written_under_the_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).reports_dir == Path("data/reports")

    monkeypatch.setenv("DATA_DIR", "/srv/avtalsagent/data")

    assert Settings(_env_file=None).reports_dir == Path("/srv/avtalsagent/data/reports")


def test_the_agents_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.agent_reasoning_effort == "low"
    assert settings.agent_model_call_limit == 16
    assert settings.citation_retries == 1
    assert settings.mcp_transport == "stdio"
    assert settings.checkpointer == "memory"


def test_the_agents_settings_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "medium")
    monkeypatch.setenv("AGENT_MODEL_CALL_LIMIT", "8")
    monkeypatch.setenv("CITATION_RETRIES", "0")

    settings = Settings(_env_file=None)

    assert settings.agent_reasoning_effort == "medium"
    assert settings.agent_model_call_limit == 8
    assert settings.citation_retries == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        # The agent model takes four levels and refuses "none" (measured in the M7 spike).
        ("AGENT_REASONING_EFFORT", "none"),
        ("AGENT_MODEL_CALL_LIMIT", "0"),
        ("CITATION_RETRIES", "-1"),
    ],
)
def test_the_agents_settings_refuse_values_out_of_range(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_returns_same_instance() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
