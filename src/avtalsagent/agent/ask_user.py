"""ask_user: the agent's question to the user, which pauses the run until the answer.

What:
    `ask_user(question, options)`, a LangChain tool that stops the run with
    a LangGraph interrupt carrying `{question, options?}` and returns the
    user's answer as text when the run is resumed.

Why:
    Some questions fit several agreements whose answers differ ("IT-drift
    Större or Mindre?"), and a guess would give a confident answer to the
    wrong one. The tool lives in the graph rather than in avtal-mcp (the
    exception in ADR 0003): it fetches no data, it pauses the run, which
    only the graph can do. The web app shows the question as AG-UI's
    standard interrupt (webbapp-kontrakt.md, clarification 1).

How:
    `interrupt()` saves the run in the checkpointer and ends it; resuming
    with `Command(resume=answer)` runs the tool again from the start, and
    `interrupt()` then returns the answer. So nothing before it may have a
    side effect. `options` goes into the payload only when given, as the
    contract's `options?` says. The answer is returned as `str`, the
    contract's plain string.
"""

from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field


class AskUserArguments(BaseModel):
    question: str = Field(description="Frågan till användaren, kort och på svenska.")
    options: list[str] | None = Field(
        default=None,
        description="Svarsalternativen, t.ex. avtalen eller delområdena frågan kan gälla.",
    )


@tool(args_schema=AskUserArguments)
def ask_user(question: str, options: list[str] | None = None) -> str:
    """Fråga användaren när frågan passar flera avtal eller delområden med olika svar.

    Ge alternativen i options. Returnerar användarens svar: ett av alternativen eller
    egen text.
    """
    payload: dict[str, Any] = {"question": question}
    if options:
        payload["options"] = options
    return str(interrupt(payload))
