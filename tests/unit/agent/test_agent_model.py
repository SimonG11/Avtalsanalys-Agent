"""Tests for avtalsagent.agent.model: the agent model's client, built without a request.

The client is only constructed; no test calls the API. The key is a dummy
value, and the tests check it never appears in the client's repr.
"""

import pytest
from pydantic import SecretStr

from avtalsagent.agent.model import MissingApiKeyError, make_agent_model
from avtalsagent.config import Settings

DUMMY_KEY = "sk-test-not-a-real-key"


def test_the_model_uses_the_responses_api_with_the_effort_and_no_temperature() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr(DUMMY_KEY),
        agent_model="the-agent-model",
        agent_reasoning_effort="medium",
    )

    model = make_agent_model(settings)

    assert model.model_name == "the-agent-model"
    assert model.use_responses_api is True
    assert model.reasoning_effort == "medium"
    assert model.temperature is None
    assert model.metadata is not None
    assert model.metadata["emit-messages"] is False
    assert isinstance(model.openai_api_key, SecretStr)
    assert model.openai_api_key.get_secret_value() == DUMMY_KEY
    assert DUMMY_KEY not in repr(model)


def test_without_a_key_the_model_is_refused_with_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(MissingApiKeyError, match="OPENAI_API_KEY saknas"):
        make_agent_model(Settings(_env_file=None))
