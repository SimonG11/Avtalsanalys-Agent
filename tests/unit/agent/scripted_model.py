"""Test doubles for running the agent's graph offline: a scripted model, readers, a reviewer.

What:
    `ScriptedModel`, a chat model that answers with prepared messages in
    order and records what it was sent; `tool_call` and `final_answer`
    build those messages, a call to an avtal-mcp tool with its `syfte`.
    `DictReader` is a `SectionReader` over a dict, `ListRegister` a
    `RegisterReader` over register rows, `DictAmendments`
    an `AmendmentReader` over a dict (no amendments for a section it does
    not have), and `ScriptedReviewer` an `AnswerReviewer` that gives
    prepared verdicts (a pass when it has none left); each records what it
    was asked.

Why:
    The graph's wiring (the hooks, the jumps, the interrupt, the tool
    errors, the answer check) can then be tested without a network, an API
    key or a database: the models' parts are fixed, so a test shows what
    the graph does with them.

How:
    `create_agent` binds the tools to the model (`bind_tools`), which here
    records their names and returns the model itself. The model is called
    through LangChain's async path, which runs `_generate`.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.ask_user import ask_user
from avtalsagent.agent.purpose import PURPOSE
from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.sections import CitedSection
from avtalsagent.validation.review import ReviewInput, ReviewVerdict

# The agent's reason for a scripted call to an avtal-mcp tool (`syfte`).
PURPOSE_TEXT = "Jag letar efter det i avtalen som svarar på frågan."


class ScriptedModel(BaseChatModel):
    """Answers each call with the next message of `script`."""

    script: list[AIMessage]
    calls: list[list[BaseMessage]] = Field(default_factory=list)  # the messages of each call
    bound: list[list[str]] = Field(default_factory=list)  # the tool names of each bind_tools

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.bound.append([convert_to_openai_tool(tool)["function"]["name"] for tool in tools])
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError("the scripted model has no more answers")
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def tool_call(
    name: str, args: dict[str, Any], call_id: str, *, purpose: str | None = PURPOSE_TEXT
) -> AIMessage:
    """An AI message calling one tool.

    A call to any tool but `ask_user` and `FinalAnswer` carries `purpose` as its `syfte`,
    as the agent's schema requires of avtal-mcp's tools, unless `args` has one or `purpose`
    is None.
    """
    if purpose is not None and name not in (ask_user.name, "FinalAnswer"):
        args = {PURPOSE: purpose, **args}
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


def final_answer(
    text: str,
    citations: Iterable[dict[str, Any]] = (),
    *,
    answered: bool = True,
    register_facts: Iterable[str] = (),
    call_id: str,
) -> AIMessage:
    """An AI message handing in a `FinalAnswer`."""
    args: dict[str, Any] = {"answered": answered, "text": text, "citations": list(citations)}
    if register_facts:
        args["register_facts"] = list(register_facts)
    return tool_call("FinalAnswer", args, call_id)


class DictReader:
    """A `SectionReader` over sections by (sha256, section_position)."""

    def __init__(self, sections: Iterable[CitedSection]) -> None:
        self.sections = {(s.sha256, s.section_position): s for s in sections}
        self.reads: list[tuple[str, int]] = []

    async def read(self, sha256: str, section_position: int) -> CitedSection | None:
        self.reads.append((sha256, section_position))
        return self.sections.get((sha256, section_position))


class ListRegister:
    """A `RegisterReader` over register rows; an agreement without rows is unknown (None)."""

    def __init__(self, entries: Iterable[RegisterEntry] = ()) -> None:
        self.entries = list(entries)
        self.reads: list[str] = []

    async def read(self, agreement_number: str) -> list[RegisterEntry] | None:
        self.reads.append(agreement_number)
        rows = [e for e in self.entries if e.agreement_number == agreement_number]
        return rows or None


class DictAmendments:
    """An `AmendmentReader` over amendments by section; None stands for a failed read."""

    def __init__(
        self, amendments: Mapping[tuple[str, int], list[AmendmentInfo] | None] | None = None
    ) -> None:
        self.amendments = dict(amendments or {})
        self.reads: list[tuple[str, int]] = []

    async def read(self, sha256: str, section_position: int) -> list[AmendmentInfo] | None:
        self.reads.append((sha256, section_position))
        return self.amendments.get((sha256, section_position), [])


PASSED = ReviewVerdict(claims=[], missing=[])


class ScriptedReviewer:
    """An `AnswerReviewer` that gives `verdicts` in order, then passes every answer."""

    def __init__(self, verdicts: Iterable[ReviewVerdict | None] = ()) -> None:
        self.verdicts = list(verdicts)
        self.requests: list[ReviewInput] = []

    async def review(self, request: ReviewInput) -> ReviewVerdict | None:
        self.requests.append(request)
        return self.verdicts.pop(0) if self.verdicts else PASSED
