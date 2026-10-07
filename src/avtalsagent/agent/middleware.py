"""CitationCheck: the step between the model's draft and the user's answer.

What:
    `CitationCheck(reader, retries)`, a `create_agent` middleware. Once per
    question it clears the last answer; when the agent's loop ends it checks
    the draft's citations (`validation.citations`) and sets `answer`:
    `verified`, `with_reservation` or `no_answer`, or sends the draft back
    to the model with what was wrong, at most `retries` times.

Why:
    The check runs after the loop, in code the model cannot get past (ADR
    0013). It reads each cited section again through `reader` rather than
    trusting the message history, which a client sends and could forge. A
    failed draft gets a new attempt (CITATION_RETRIES, one by default), since
    a misquoted word is usually easy to fix; after that the user gets the
    answer with reservation, each failed source marked, rather than
    nothing. The feedback replaces the draft's tool result instead of being
    a new message: a human message would show in the web app as if the user
    had written it.

How:
    `abefore_agent` runs once per question (not when a run resumes after
    `ask_user`) and sets `answer` to None and the retry count to 0.
    `aafter_agent` runs when the loop ends. Without a draft there are two
    cases. The model's last message called FinalAnswer, but ToolStrategy
    refused the arguments (a malformed hash, two drafts) and, because the
    message also called another tool, ended the loop all the same: the hook
    jumps back to the model, which then reads why, as it would have without
    the other call. Otherwise the model-call limit ended the loop: that is
    `no_answer`, and the AI message without tool calls that ended it (the
    limit's English note) is replaced by the answer's words, so the chat
    does not show it. With a draft, each cited
    (sha256, section_position) is read once, in order, and checked, also
    in a draft with `answered: false`: "it is not in the agreements" can
    still cite where the matter is left to the call-off, and a [n] in its
    text needs its source like any other. The status follows from the
    draft and the check (`_status`): `answered: false` is `no_answer`, a
    passing draft with sources `verified`, and one without sources or with
    a failed source `with_reservation`. A failing draft, while retries remain,
    becomes a tool result with status error under the id of
    ToolStrategy's "Svaret är lämnat" message (so `add_messages` replaces
    it), and the hook jumps back to the model. The hooks are async only: a
    new question run synchronously (`invoke`, `stream`) stops at once with
    an error, before any model call; a synchronous resume after `ask_user`
    does not pass `before_agent` and fails only at the check.
"""

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.runtime import Runtime

from avtalsagent.agent.schemas import Answer, AnswerStatus, AvtalState, FinalAnswer
from avtalsagent.agent.sections import CitedSection, SectionReader
from avtalsagent.validation.citations import CitationReport, check_citations

# The structured-output tool's name, which ToolStrategy gives its tool result too.
FINAL_ANSWER_TOOL = FinalAnswer.__name__

NO_DRAFT_TEXT = (
    "Jag kunde inte ta fram ett svar på frågan. Försök gärna igen, med en mer avgränsad fråga."
)
SYNC_RUN_ERROR = (
    "The agent's citation check reads sections through the async MCP client: run the graph "
    "with ainvoke or astream, not invoke or stream."
)


class CitationCheck(AgentMiddleware[AvtalState]):
    """Checks the draft's citations and sets the answer; see the module docstring."""

    state_schema = AvtalState

    def __init__(self, reader: SectionReader, retries: int) -> None:
        super().__init__()
        if retries < 0:
            raise ValueError(f"retries must be 0 or more, not {retries}")
        self._reader = reader
        self._retries = retries

    def before_agent(self, state: AvtalState, runtime: Runtime[None]) -> dict[str, Any] | None:
        raise RuntimeError(SYNC_RUN_ERROR)

    async def abefore_agent(self, state: AvtalState, runtime: Runtime[None]) -> dict[str, Any]:
        return {"answer": None, "structured_response": None, "citation_retries": 0}

    @hook_config(can_jump_to=["model"])
    async def aafter_agent(self, state: AvtalState, runtime: Runtime[None]) -> dict[str, Any]:
        draft = state.get("structured_response")
        if draft is None:
            if _draft_refused(state["messages"]):
                return {"jump_to": "model"}
            return _no_draft(state["messages"])

        sections: dict[tuple[str, int], CitedSection | None] = {}
        for source in draft.citations:
            key = (source.sha256, source.section_position)
            if key not in sections:
                sections[key] = await self._reader.read(*key)
        report = check_citations(draft, sections)

        retries = state.get("citation_retries", 0)
        if report.problems and retries < self._retries:
            return {
                "messages": [_feedback(state["messages"], report.problems)],
                "structured_response": None,
                "citation_retries": retries + 1,
                "jump_to": "model",
            }
        status = _status(draft, report)
        return {"answer": Answer(text=draft.text, status=status, citations=report.citations)}


def _status(draft: FinalAnswer, report: CitationReport) -> AnswerStatus:
    """The answer's status: whether it answers, and whether every source passed."""
    if not draft.answered:
        return "no_answer"
    if report.problems or not report.citations:
        return "with_reservation"
    return "verified"


def _draft_refused(messages: Sequence[AnyMessage]) -> bool:
    """The model's last message called FinalAnswer, yet there is no draft: its form was refused.

    The limit's note, the other way a loop ends without a draft, has no tool calls.
    """
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    return last is not None and any(c["name"] == FINAL_ANSWER_TOOL for c in last.tool_calls)


def _no_draft(messages: Sequence[AnyMessage]) -> dict[str, Any]:
    """`no_answer` for a loop that ended without a draft."""
    update: dict[str, Any] = {
        "answer": Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[])
    }
    last = messages[-1] if messages else None
    # ModelCallLimitMiddleware ends the loop with an English note as an AI message (so would
    # a model that wrote text instead of a draft), which the chat would show; the same id
    # replaces it with the answer's words.
    if isinstance(last, AIMessage) and not last.tool_calls and last.id is not None:
        update["messages"] = [AIMessage(id=last.id, content=NO_DRAFT_TEXT)]
    return update


def _feedback(messages: Sequence[AnyMessage], problems: Sequence[str]) -> ToolMessage:
    """The draft's tool result, replaced by what the check found wrong."""
    submitted = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, ToolMessage) and message.name == FINAL_ANSWER_TOOL
        ),
        None,
    )
    if submitted is None or submitted.id is None:
        # ToolStrategy adds this message with every draft; without it, the feedback would be
        # a second result for one tool call, which the model's API refuses.
        raise RuntimeError(f"no {FINAL_ANSWER_TOOL} tool result to put the feedback in")
    listed = "\n".join(f"- {problem}" for problem in problems)
    return ToolMessage(
        id=submitted.id,
        tool_call_id=submitted.tool_call_id,
        name=FINAL_ANSWER_TOOL,
        status="error",
        content=(
            f"Kontrollen av källorna underkände svaret:\n{listed}\n"
            "Rätta svaret och anropa FinalAnswer igen: läs avsnittet med read_section och "
            "kopiera citatet ordagrant ur texten, eller ta bort påståendet och dess källa."
        ),
    )
