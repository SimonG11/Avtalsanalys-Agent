"""The agent's language model: the configured OpenAI model, set up for tools.

What:
    `make_agent_model(settings)` gives the `ChatOpenAI` client of the agent
    model (`AGENT_MODEL`, ADR 0005) at the configured reasoning effort.
    `MissingApiKeyError` is raised when no OpenAI key is set.

Why:
    The agent model takes tools only through the Responses API when it also
    reasons, and rejects any temperature but its default (both measured in
    the M7 spike), so the client is set up once, here. The structured
    answer must not reach the web app as chat text before it is checked
    (webbapp-kontrakt.md, clarification 2).

How:
    `use_responses_api=True`, so every call uses one API and one message
    format; `reasoning_effort` from `AGENT_REASONING_EFFORT`; no temperature.
    The metadata `emit-messages: False` tells the AG-UI adapter not to
    stream the model's messages; the tool calls are still streamed as
    steps. The key goes from pydantic-settings, as a `SecretStr`, only to
    `ChatOpenAI`; it is never printed or logged.
"""

from langchain_openai import ChatOpenAI

from avtalsagent.config import Settings


class MissingApiKeyError(RuntimeError):
    """OPENAI_API_KEY is not set, so the agent cannot call its model."""


def make_agent_model(settings: Settings) -> ChatOpenAI:
    """The agent's chat model; raises `MissingApiKeyError` without an OpenAI key."""
    if settings.openai_api_key is None:
        raise MissingApiKeyError(
            "OPENAI_API_KEY saknas: agenten behöver en nyckel till OpenAI. Sätt den i .env "
            "eller som miljövariabel."
        )
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key,
        use_responses_api=True,
        reasoning_effort=settings.agent_reasoning_effort,
        metadata={"emit-messages": False},
    )
