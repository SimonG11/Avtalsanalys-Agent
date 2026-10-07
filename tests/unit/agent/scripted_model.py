"""Test doubles for running the agent's graph offline: a scripted model and a section reader.

What:
    `ScriptedModel`, a chat model that answers with prepared messages in
    order and records what it was sent; `tool_call` and `final_answer`
    build those messages. `DictReader` is a `SectionReader` over a dict and
    records what it was asked.

Why:
    The graph's wiring (the hooks, the jumps, the interrupt, the tool
    errors) can then be tested without a network, an API key or a database:
    the model's part is fixed, so a test shows what the graph does with it.

How:
    `create_agent` binds the tools to the model (`bind_tools`), which here
    records their names and returns the model itself. The model is called
    through LangChain's async path, which runs `_generate`.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from avtalsagent.agent.sections import CitedSection


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


def tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    """An AI message calling one tool."""
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


def final_answer(
    text: str, citations: Iterable[dict[str, Any]] = (), *, answered: bool = True, call_id: str
) -> AIMessage:
    """An AI message handing in a `FinalAnswer`."""
    args = {"answered": answered, "text": text, "citations": list(citations)}
    return tool_call("FinalAnswer", args, call_id)


class DictReader:
    """A `SectionReader` over sections by (sha256, section_position)."""

    def __init__(self, sections: Iterable[CitedSection]) -> None:
        self.sections = {(s.sha256, s.section_position): s for s in sections}
        self.reads: list[tuple[str, int]] = []

    async def read(self, sha256: str, section_position: int) -> CitedSection | None:
        self.reads.append((sha256, section_position))
        return self.sections.get((sha256, section_position))
