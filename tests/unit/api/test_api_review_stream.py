"""Tests that the AG-UI stream never shows the reviewer's verdict.

The reviewer runs in a step of the agent's graph after the agent's loop,
as the answer check runs it. Its fake model streams the verdict both as
text and as a `ReviewVerdict` tool call, and has neither
`disable_streaming` nor metadata of its own, so only the call's metadata
keeps it out of the stream. The graph runs through the API's AG-UI agent
(`make_agui_agent`), and the events are encoded as the browser gets them.
A control makes the same call without that metadata and sees the verdict
in the stream, so the test can see a leak. No network, key or database is
used.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from ag_ui.encoder import EventEncoder
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from avtalsagent.agent.reviewer import ModelReviewer, review_messages
from avtalsagent.api.agui import make_agui_agent
from avtalsagent.validation.review import ReviewInput, ReviewVerdict
from tests.unit.agent.scripted_model import ScriptedModel
from tests.unit.agent.test_reviewer import REQUEST, VERDICT, VERDICT_ARGS

# In the verdict's text, so any event that carries the verdict shows it.
SECRET = "GRANSKARENS-EGEN-TEXT"
VERDICT_JSON = json.dumps(VERDICT_ARGS | {"missing": [SECRET]}, ensure_ascii=False)

Review = Callable[[BaseChatModel, ReviewInput], Awaitable[object]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StreamingReviewModel(BaseChatModel):
    """A reviewer model that streams its verdict as text, then as a ReviewVerdict tool call."""

    @property
    def _llm_type(self) -> str:
        return "streaming-review"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        call = {"name": "ReviewVerdict", "args": json.loads(VERDICT_JSON), "id": "rv1"}
        message = AIMessage(content=VERDICT_JSON, tool_calls=[call | {"type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        half = len(VERDICT_JSON) // 2
        chunks = [
            AIMessageChunk(content=VERDICT_JSON[:half]),
            AIMessageChunk(content=VERDICT_JSON[half:]),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": "ReviewVerdict", "args": "", "id": "rv1", "index": 0},
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": None, "args": VERDICT_JSON, "id": None, "index": 0}],
            ),
        ]
        for chunk in chunks:
            yield ChatGenerationChunk(message=chunk)


class ReviewStep(AgentMiddleware[AgentState[Any]]):
    """Reviews after the agent's loop, as the answer check does, and keeps the verdicts."""

    def __init__(self, model: BaseChatModel, review: Review) -> None:
        super().__init__()
        self.model = model
        self.review = review
        self.verdicts: list[object] = []

    async def aafter_agent(
        self, state: AgentState[Any], runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        self.verdicts.append(await self.review(self.model, REQUEST))
        return None


async def through_the_reviewer(model: BaseChatModel, request: ReviewInput) -> object:
    return await ModelReviewer(model).review(request)


async def without_the_metadata(model: BaseChatModel, request: ReviewInput) -> object:
    """The reviewer's call without its config: what the reviewer must not do."""
    judge = model.with_structured_output(
        ReviewVerdict, method="json_schema", strict=True, include_raw=True
    )
    result = await judge.ainvoke(review_messages(request))
    assert isinstance(result, dict)
    return result["parsed"]


async def run_with(review: Review) -> tuple[list[dict[str, Any]], list[object]]:
    """The run's events as the browser reads them, and the verdicts the step got."""
    # The agent's loop ends at once; the step after it is what the test watches.
    agent_model = ScriptedModel(script=[AIMessage(content="", id="agent-answer")])
    step = ReviewStep(StreamingReviewModel(), review)
    graph = create_agent(agent_model, tools=[], middleware=[step], checkpointer=InMemorySaver())
    agent = make_agui_agent(graph)
    run = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        state={},
        messages=[UserMessage(id="u1", role="user", content="Vilken uppsägningstid gäller?")],
        tools=[],
        context=[],
        forwarded_props={},
    )
    encoder = EventEncoder()
    events = [
        json.loads(encoder.encode(event).removeprefix("data: ")) async for event in agent.run(run)
    ]
    return events, step.verdicts


def leaks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events that show the reviewer: chat text, a ReviewVerdict call, or its words."""
    return [
        event
        for event in events
        if event["type"].startswith("TEXT_MESSAGE")
        or (event["type"].startswith("TOOL_CALL") and "ReviewVerdict" in json.dumps(event))
        or SECRET in json.dumps(event, ensure_ascii=False)
    ]


@pytest.mark.anyio
async def test_the_reviewers_verdict_never_reaches_the_stream() -> None:
    events, verdicts = await run_with(through_the_reviewer)

    assert verdicts == [VERDICT.model_copy(update={"missing": [SECRET]})]
    assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED"
    assert leaks(events) == []
    assert not [event for event in events if event["type"].startswith("TOOL_CALL")]


@pytest.mark.anyio
async def test_the_same_call_without_the_metadata_would_show_the_verdict() -> None:
    events, verdicts = await run_with(without_the_metadata)

    assert verdicts == [VERDICT.model_copy(update={"missing": [SECRET]})]
    kinds = {event["type"] for event in leaks(events)}
    assert {"TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TOOL_CALL_START"} <= kinds
    shown = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert SECRET in shown
