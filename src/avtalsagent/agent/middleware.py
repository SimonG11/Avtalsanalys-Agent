"""AnswerCheck: the step between the model's draft and the user's answer.

What:
    `AnswerCheck(reader, register, amendments, reviewer, retries, today)`, a
    `create_agent` middleware. Once per question it clears the last answer;
    when the agent's loop ends it checks the draft (`validation.chain`: the
    citations, the register facts, the latest wording and the review) and
    sets `answer`: `verified`, `with_reservation` or `no_answer`, or sends
    the draft back to the model with what was wrong, at most `retries`
    times.

Why:
    The check runs after the loop, in code the model cannot get past (ADR
    0013, 0015). It reads each cited section, its amendments and each
    declared agreement again through avtal-mcp rather than trusting the
    message history, which a client sends and could forge. A failed draft
    gets a new attempt (VALIDATION_RETRIES, two by default, so at most
    three drafts), since a misquoted word, a wrong date, an old wording or
    a missing part is usually easy to fix; after that the user gets the
    answer with reservation, each failed source marked and the reasons
    noted, rather than nothing. The feedback replaces the draft's tool
    result instead of being a new message: a human message would show in
    the web app as if the user had written it.

How:
    `abefore_agent` runs once per question (not when a run resumes after
    `ask_user`) and sets `answer` and the fallback to None and the retry
    count to 0. `aafter_agent` runs when the loop ends. Without a draft
    there are two cases. The model's last message called FinalAnswer, but
    ToolStrategy refused the arguments (a malformed hash, two drafts) and,
    because the message also called another tool, ended the loop all the
    same: the hook jumps back to the model, which then reads why, as it
    would have without the other call. Otherwise the model-call limit ended
    the loop: the answer the last failed draft would have got, if a draft
    failed in this question, else `no_answer`; the AI message without tool
    calls that ended it (the limit's English note) is replaced by the
    answer's words, so the chat does not show it. With a draft, the chain
    runs (`check_answer`), also on a draft with `answered: false`: "it is
    not in the agreements" can still cite where the matter is left to the
    call-off, and a [n] in its text needs its source like any other. The
    question is the last human message; the follow-ups are the `ask_user`
    results after it, each with the question the agent asked (a cancelled
    question is none). A failing draft, while retries remain,
    becomes a tool result with status error under the id of ToolStrategy's
    "Svaret är lämnat" message (so `add_messages` replaces it), and the
    hook jumps back to the model. A review that could not be made, or
    amendments that could not be read, use no retry. The status follows
    from the draft and the report (`_status`): `answered: false` is
    `no_answer`; problems left, a failed review, unread amendments or no
    source to check is `with_reservation`; anything else `verified`. The
    hooks are async only: a new question run synchronously (`invoke`,
    `stream`) stops at once with an error, before any model call; a
    synchronous resume after `ask_user` does not pass `before_agent` and
    fails only at the check.
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from avtalsagent.agent.amendments import AmendmentReader
from avtalsagent.agent.ask_user import NOT_ANSWERED, ask_user
from avtalsagent.agent.register_reader import RegisterReader
from avtalsagent.agent.schemas import Answer, AnswerStatus, AvtalState, FinalAnswer
from avtalsagent.agent.sections import SectionReader
from avtalsagent.validation.chain import ChainReport, check_answer
from avtalsagent.validation.review import AnswerReviewer, FollowUp

# The structured-output tool's name, which ToolStrategy gives its tool result too.
FINAL_ANSWER_TOOL = FinalAnswer.__name__

NO_DRAFT_TEXT = (
    "Jag kunde inte ta fram ett svar på frågan. Försök gärna igen, med en mer avgränsad fråga."
)
SYNC_RUN_ERROR = (
    "The agent's answer check reads through the async MCP client and reviews with an async "
    "model call: run the graph with ainvoke or astream, not invoke or stream."
)
# The note for an answer with reservation whose report named nothing for the user; the chain
# gives a note for every reason it knows, so this is a fallback.
UNCHECKED_NOTE = "Svaret kunde inte kontrolleras fullt ut."
FIX_INSTRUCTION = (
    "Rätta svaret och anropa FinalAnswer igen. Kopiera ett citat ordagrant ur avsnittets "
    "text från read_section. Kopiera en uppgift ur registret från search_register och lägg "
    "avtalets nummer i register_facts. Det som inte går att belägga tar du bort, eller skriver "
    "att det inte framgår."
)


class AnswerCheck(AgentMiddleware[AvtalState]):
    """Checks the draft and sets the answer; see the module docstring."""

    state_schema = AvtalState

    def __init__(
        self,
        reader: SectionReader,
        register: RegisterReader,
        amendments: AmendmentReader,
        reviewer: AnswerReviewer,
        retries: int,
        today: Callable[[], date],
    ) -> None:
        super().__init__()
        if retries < 0:
            raise ValueError(f"retries must be 0 or more, not {retries}")
        self._reader = reader
        self._register = register
        self._amendments = amendments
        self._reviewer = reviewer
        self._retries = retries
        self._today = today

    def before_agent(self, state: AvtalState, runtime: Runtime[None]) -> dict[str, Any] | None:
        raise RuntimeError(SYNC_RUN_ERROR)

    async def abefore_agent(self, state: AvtalState, runtime: Runtime[None]) -> dict[str, Any]:
        return {
            "answer": None,
            "structured_response": None,
            "validation_retries": 0,
            "fallback_answer": None,
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_agent(self, state: AvtalState, runtime: Runtime[None]) -> dict[str, Any]:
        messages = state["messages"]
        draft = state.get("structured_response")
        if draft is None:
            if _draft_refused(messages):
                return {"jump_to": "model"}
            return _no_draft(messages, state.get("fallback_answer"))

        question, follow_ups = _question(messages)
        report = await check_answer(
            draft,
            sections=self._reader,
            register=self._register,
            amendments=self._amendments,
            reviewer=self._reviewer,
            question=question,
            follow_ups=follow_ups,
            today=self._today(),
        )
        answer = _answer(draft, report)
        retries = state.get("validation_retries", 0)
        if report.problems and retries < self._retries:
            attempt = f"försök {retries + 1} av {self._retries + 1}"
            return {
                "messages": [_feedback(messages, report.problems, attempt)],
                "structured_response": None,
                "validation_retries": retries + 1,
                "fallback_answer": answer,
                "jump_to": "model",
            }
        return {"answer": answer, "fallback_answer": None}


def _answer(draft: FinalAnswer, report: ChainReport) -> Answer:
    """The answer the draft gets as it is, with the report's sources, rows and notes."""
    status = _status(draft, report)
    reservations: list[str] = []
    if status == "with_reservation":
        reservations = report.reservations or [UNCHECKED_NOTE]
    return Answer(
        text=draft.text,
        status=status,
        citations=report.citations,
        reservations=reservations,
        register_facts=report.register_facts,
    )


def _status(draft: FinalAnswer, report: ChainReport) -> AnswerStatus:
    """The answer's status: whether it answers, and whether everything in it was checked."""
    if not draft.answered:
        return "no_answer"
    unchecked = report.review_failed or report.amendments_unread or not report.has_source
    if report.problems or unchecked:
        return "with_reservation"
    return "verified"


def _question(messages: Sequence[AnyMessage]) -> tuple[str, list[FollowUp]]:
    """The question (the last human message) and the `ask_user` questions answered after it."""
    start = next(
        (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)),
        None,
    )
    if start is None:
        return "", []
    asked = {
        call["id"]: str(call["args"].get("question", ""))
        for message in messages[start + 1 :]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == ask_user.name
    }
    follow_ups = [
        FollowUp(question=asked.get(message.tool_call_id, ""), answer=str(message.text))
        for message in messages[start + 1 :]
        if isinstance(message, ToolMessage)
        and message.name == ask_user.name
        and message.text != NOT_ANSWERED
    ]
    return str(messages[start].text), follow_ups


def _draft_refused(messages: Sequence[AnyMessage]) -> bool:
    """The model's last message called FinalAnswer, yet there is no draft: its form was refused.

    The limit's note, the other way a loop ends without a draft, has no tool calls.
    """
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    return last is not None and any(c["name"] == FINAL_ANSWER_TOOL for c in last.tool_calls)


def _no_draft(messages: Sequence[AnyMessage], fallback: Answer | None) -> dict[str, Any]:
    """The answer for a loop that ended without a draft: the last failed draft's, or none."""
    answer = fallback or Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[])
    update: dict[str, Any] = {"answer": answer, "fallback_answer": None}
    last = messages[-1] if messages else None
    # ModelCallLimitMiddleware ends the loop with an English note as an AI message (so would
    # a model that wrote text instead of a draft), which the chat would show; the same id
    # replaces it with the answer's words.
    if isinstance(last, AIMessage) and not last.tool_calls and last.id is not None:
        update["messages"] = [AIMessage(id=last.id, content=answer.text)]
    return update


def _feedback(messages: Sequence[AnyMessage], problems: Sequence[str], attempt: str) -> ToolMessage:
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
        content=f"Kontrollen underkände svaret ({attempt}):\n{listed}\n{FIX_INSTRUCTION}",
    )
