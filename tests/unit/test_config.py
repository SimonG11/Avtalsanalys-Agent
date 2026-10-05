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
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_docker_compose_and_adr_0005() -> None:
    settings = Settings(_env_file=None)

    assert str(settings.database_url) == (
        "postgresql://avtalsagent:avtalsagent@localhost:5432/avtalsagent"
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


def test_empty_value_counts_as_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert Settings(_env_file=None).openai_api_key is None


def test_invalid_database_url_fails_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-url")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_openai_key_is_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-not-a-real-key"
    assert "sk-test-not-a-real-key" not in repr(settings)
    assert "sk-test-not-a-real-key" not in str(settings.model_dump())


def test_get_settings_returns_same_instance() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
