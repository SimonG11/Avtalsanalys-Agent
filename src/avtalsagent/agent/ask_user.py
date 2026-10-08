"""ask_user: the agent's question to the user, which pauses the run until the answer.

What:
    `ask_user(question, options)`, a LangChain tool that stops the run with
    a LangGraph interrupt carrying `{question, options}` and returns the
    user's answer as text when the run is resumed. `options` is 2-5 short
    alternatives, always.

Why:
    Some questions depend on facts the agent cannot enumerate (the user's
    supplier, role or contract value), or fit too many agreements to answer
    for each, and a guess would give a confident answer to the wrong one.
    The agent asks only then (the prompt's rule 4, ADR 0025); the
    alternatives let the web app show one button per answer in the chat,
    with a field for the user's own text. The tool lives in the graph
    rather than in avtal-mcp (the exception in ADR 0003): it fetches no
    data, it pauses the run, which only the graph can do. The web app shows
    the question as AG-UI's standard interrupt (webbapp-kontrakt.md,
    clarification 1).

How:
    The arguments' schema requires 2-5 non-empty options, so a call
    without them is refused before the tool runs and the model reads why
    and can call again; the run goes on. `interrupt()` saves the run in the
    checkpointer and ends it; resuming with `Command(resume=answer)` runs
    the tool again from the start, and `interrupt()` then returns the
    answer. So nothing before it may have a side effect. The answer is
    returned as `str`, the contract's plain string: one of the options or
    the user's own text. A question the client cancels instead (AG-UI's
    resume with status "cancelled") comes back as ag-ui-langgraph's cancel
    mark, a dict; the model then reads that the user did not answer, not
    the dict.
"""

from collections.abc import Mapping
from typing import Annotated

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field, StringConstraints

# The key ag-ui-langgraph sets in the resume value of a cancelled question
# (ag_ui_langgraph.interrupts.DEFAULT_RESUME_SENTINEL_CANCELLED); the agent does not import
# the web transport, and a test checks that the two agree.
CANCELLED_MARK = "__agui_cancelled__"
NOT_ANSWERED = (
    "Användaren svarade inte på frågan. Svara så långt det går utan svaret, och säg vad "
    "som avgör svaret."
)


# How many alternatives a question gives: two at least, or there is nothing to choose, and few
# enough to read at a glance as buttons.
MIN_OPTIONS = 2
MAX_OPTIONS = 5


class AskUserArguments(BaseModel):
    question: str = Field(description="Frågan till användaren, kort och på svenska.")
    options: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]] = Field(
        min_length=MIN_OPTIONS,
        max_length=MAX_OPTIONS,
        description=(
            "2-5 korta svarsalternativ som delar upp det som avgör svaret, till exempel "
            "leverantörerna, rollerna eller beloppsgränserna. Användaren kan också svara med "
            "egen text."
        ),
    )


@tool(args_schema=AskUserArguments)
def ask_user(question: str, options: list[str]) -> str:
    """Fråga användaren när svaret beror på användarens eget fall eller på för många alternativ.

    Är alternativen få och svaren korta, svara i stället för vart och ett. Ge 2-5 korta
    alternativ i options. Returnerar användarens svar: ett av alternativen eller egen text.
    """
    answer = interrupt({"question": question, "options": options})
    if isinstance(answer, Mapping) and answer.get(CANCELLED_MARK):
        return NOT_ANSWERED
    return str(answer)
