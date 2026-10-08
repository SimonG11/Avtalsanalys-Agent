"""The agent's language model: the configured OpenAI model, set up for tools.

What:
    `make_agent_model(settings)` gives the `ChatOpenAI` client of the agent
    model (`AGENT_MODEL`, ADR 0005) at the configured reasoning effort, asking
    for a summary of its reasoning. `MissingApiKeyError` is raised when no
    OpenAI key is set.

Why:
    The agent model takes tools only through the Responses API when it also
    reasons, and rejects any temperature but its default (both measured in
    the M7 spike), so the client is set up once, here. The structured
    answer must not reach the web app as chat text before it is checked
    (webbapp-kontrakt.md, clarification 2). The web app shows how the agent
    thinks between its steps (ADR 0025); OpenAI gives a reasoning model's
    summary of its reasoning, never the reasoning itself, and only when asked.

How:
    `use_responses_api=True`, so every call uses one API and one message
    format; `reasoning` with the effort from `AGENT_REASONING_EFFORT` and the
    summary from `AGENT_REASONING_SUMMARY` ("off" leaves it out); no
    temperature. The metadata `emit-messages: False` tells the AG-UI adapter
    not to stream the model's messages; the tool calls are still streamed as
    steps, and the adapter streams the summary as AG-UI's reasoning events,
    which that metadata does not cover. The summary stays in the model's
    message as a reasoning item, and langchain-openai sends it back with the
    item's id in the next call; OpenAI finds the item by its id, since it
    stores responses by default (`store`).
    The key goes from pydantic-settings, as a `SecretStr`, only to
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
    reasoning: dict[str, str] = {"effort": settings.agent_reasoning_effort}
    if settings.agent_reasoning_summary != "off":
        reasoning["summary"] = settings.agent_reasoning_summary
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key,
        use_responses_api=True,
        reasoning=reasoning,
        metadata={"emit-messages": False},
    )
