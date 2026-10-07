"""Tests for avtalsagent.api: the agent over AG-UI, run through the whole app.

Each test starts the app with its lifespan (`create_app` with a scripted
model and reviewer, a search tool, sections and register rows in memory, and
an in-memory checkpointer)
and posts AG-UI's `RunAgentInput` to `/agui` through httpx's ASGI transport,
reading the server-sent events as the web app does. No network, key or
database is used.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import anyio
import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from avtalsagent.agent.ask_user import NOT_ANSWERED
from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.sections import CitedSection
from avtalsagent.api.agui import RUN_FAILED
from avtalsagent.api.app import OpenTracing, create_app
from avtalsagent.api.documents import DocumentFiles, StoredFile
from avtalsagent.config import Settings
from avtalsagent.observability.tracing import open_tracing
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)

SHA = "a1" * 32
QUOTE = "uppsägningstid om tre (3) månader"
SECTION = CitedSection(
    sha256=SHA,
    section_position=41,
    section_number=None,  # text before the first numbered heading
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Större, fler än 200 anställda"],
    page_start=None,  # a Word file
    text=f"Kontraktet kan sägas upp av Kunden med en {QUOTE}.",
)
GOOD = {"id": 1, "sha256": SHA, "section_position": 41, "quote": QUOTE}
PASSWORD = "hemligt-lösen-42"
OPTIONS = ["IT-drift Större", "IT-drift Mindre"]
# The settings every test's app runs with.
SETTINGS = Settings(
    _env_file=None, database_url=f"postgresql+psycopg://avtalsagent:{PASSWORD}@db:5432/x"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@tool
def search_documents(query: str) -> str:
    """Sök i dokumenten."""
    return f"Träff: sha256 {SHA}, section_position 41, avsnitt Uppsägning."


@tool
def broken_search(query: str) -> str:
    """Sök i dokumenten."""
    raise RuntimeError(f"connection to postgresql://avtalsagent:{PASSWORD}@db:5432 failed")


class NoDocuments:
    def find(self, sha256: str) -> StoredFile | None:
        return None


class Sessions:
    """Stands in for avtal-mcp: counts the sessions opened and closed, and can refuse some."""

    def __init__(self, tools: list[BaseTool], refuse: set[int] | None = None) -> None:
        self.tools = tools
        self.refuse = refuse or set()  # which sessions (1, 2, ...) fail to open
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def open(self, settings: Settings) -> AsyncIterator[McpTools]:
        self.opened += 1
        if self.opened in self.refuse:
            raise OSError(f"All connection attempts failed (postgresql://u:{PASSWORD}@db)")
        try:
            yield McpTools(
                tools=self.tools,
                reader=DictReader([SECTION]),
                register=ListRegister(),
                amendments=DictAmendments(),
            )
        finally:
            self.closed += 1


class BreakingSessions:
    """Stands in for the MCP client over HTTP when avtal-mcp goes away during a tool call.

    The client sends each call from a task of its own in the session's task
    group. When that task fails, the group cancels the run, and the session
    raises the error when it closes. Sessions in `breaks` (1, 2, ...) fail
    so at their first tool call; the others answer.
    """

    def __init__(self, breaks: set[int]) -> None:
        self.breaks = breaks
        self.opened = 0

    @asynccontextmanager
    async def open(self, settings: Settings) -> AsyncIterator[McpTools]:
        self.opened += 1
        called = anyio.Event()

        async def send_calls() -> None:
            await called.wait()
            raise httpx.ConnectError("avtal-mcp went away")

        @tool
        async def search_documents(query: str) -> str:
            """Sök i dokumenten."""
            called.set()
            await anyio.sleep_forever()
            return ""

        tools = [search_documents] if self.opened in self.breaks else [answering_search]
        async with anyio.create_task_group() as group:
            group.start_soon(send_calls)
            try:
                yield McpTools(
                    tools=tools,
                    reader=DictReader([SECTION]),
                    register=ListRegister(),
                    amendments=DictAmendments(),
                )
            finally:
                group.cancel_scope.cancel()  # a session that did not fail closes quietly


@tool("search_documents")
def answering_search(query: str) -> str:
    """Sök i dokumenten."""
    return f"Träff: sha256 {SHA}, section_position 41, avsnitt Uppsägning."


def app_with(
    script: list[AIMessage],
    *,
    tools: list[BaseTool] | None = None,
    sessions: Sessions | BreakingSessions | None = None,
    open_trace: OpenTracing = open_tracing,
) -> tuple[FastAPI, ScriptedModel]:
    model = ScriptedModel(script=script)
    open_tools = (sessions or Sessions(tools or [search_documents])).open

    @asynccontextmanager
    async def open_saver(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
        yield InMemorySaver(serde=serializer())

    @contextmanager
    def open_documents(settings: Settings) -> Iterator[DocumentFiles]:
        yield NoDocuments()

    app = create_app(
        SETTINGS,
        make_model=lambda settings: model,
        make_answer_reviewer=lambda settings: ScriptedReviewer(),
        open_tools=open_tools,
        open_saver=open_saver,
        open_documents=open_documents,
        open_trace=open_trace,
    )
    return app, model


@asynccontextmanager
async def client_of(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def run_input(run_id: str, messages: list[dict[str, Any]], **more: Any) -> dict[str, Any]:
    return {
        "threadId": "t1",
        "runId": run_id,
        "state": {},
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
        **more,
    }


QUESTION = [{"id": "u1", "role": "user", "content": "Vilken uppsägningstid gäller?"}]


async def post(client: httpx.AsyncClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's events, as the web app reads them from the stream."""
    events = []
    headers = {"Accept": "text/event-stream"}
    async with client.stream("POST", "/agui", json=body, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def of_type(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == kind]


@pytest.mark.anyio
async def test_a_question_streams_the_steps_and_ends_with_the_checked_answer() -> None:
    app, _ = app_with(
        [
            tool_call("search_documents", {"query": "uppsägningstid"}, "c1"),
            final_answer("Uppsägningstiden är tre månader [1].", [GOOD], call_id="c2"),
        ]
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert "outcome" not in events[-1]  # not an interrupt
    started = [event["toolCallName"] for event in of_type(events, "TOOL_CALL_START")]
    assert started[0] == "search_documents"
    answer = of_type(events, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]
    assert answer == {
        "text": "Uppsägningstiden är tre månader [1].",
        "status": "verified",
        "citations": [
            {
                "id": 1,
                "sha256": SHA,
                "file_title": "Allmänna villkor",
                "page_title": "IT-drift Större, fler än 200 anställda",
                # Null, not left out: the contract's nullable fields (clarification 15).
                "section_number": None,
                "section_title": "Uppsägning",
                "page": None,
                "quote": QUOTE,
                "verified": True,
            }
        ],
        "reservations": [],
        "register_facts": [],
    }


@pytest.mark.anyio
async def test_a_question_with_many_steps_is_not_stopped_by_langchains_default_limit() -> None:
    # A model call is about four of LangGraph's steps with the middleware. With LangChain's
    # default recursion limit (25) a run in the web app stopped after about six model calls.
    searches = [tool_call("search_documents", {"query": f"sökning {n}"}, f"c{n}") for n in range(8)]
    app, model = app_with(
        [*searches, final_answer("Uppsägningstiden är tre månader [1].", [GOOD], call_id="c9")]
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "RUN_ERROR") == []
    assert events[-1]["type"] == "RUN_FINISHED"
    assert len(of_type(events, "TOOL_CALL_START")) >= 8
    assert of_type(events, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert model.script == []  # every scripted call was made


@pytest.mark.anyio
async def test_a_model_that_never_answers_is_stopped_by_the_model_call_limit() -> None:
    # The run's recursion limit is the graph's own 9 999 (above). What ends a loop that never
    # hands in an answer is ModelCallLimitMiddleware: a finished run without an answer.
    limit = SETTINGS.agent_model_call_limit
    searches = [
        tool_call("search_documents", {"query": f"sökning {n}"}, f"c{n}") for n in range(2 * limit)
    ]
    app, model = app_with(searches)

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "RUN_ERROR") == []
    assert events[-1]["type"] == "RUN_FINISHED"
    assert "outcome" not in events[-1]
    assert len(model.calls) == limit
    assert of_type(events, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "no_answer"


@pytest.mark.anyio
async def test_with_tracing_a_run_is_a_trace_in_the_threads_session() -> None:
    exporter = InMemorySpanExporter()
    keys = Settings(
        _env_file=None,
        langfuse_public_key=f"pk-lf-{uuid.uuid4()}",
        langfuse_secret_key=SecretStr("sk-lf-test-not-a-real-key"),
        langfuse_base_url="http://127.0.0.1:9",  # never reached: the spans stay in memory
    )
    # Eight searches: the tracing's config keeps the graph's own recursion limit.
    searches = [tool_call("search_documents", {"query": f"sökning {n}"}, f"c{n}") for n in range(8)]
    app, _ = app_with(
        [*searches, final_answer("Uppsägningstiden är tre månader [1].", [GOOD], call_id="c9")],
        open_trace=lambda settings: open_tracing(keys, span_exporter=exporter),
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))
    # The app's lifespan closed the tracing, which sent what was left.

    assert of_type(events, "RUN_ERROR") == []
    assert events[-1]["type"] == "RUN_FINISHED"
    spans = exporter.get_finished_spans()
    assert len({span.context.trace_id for span in spans}) == 1
    # The run's root carries the trace's name; FastAPI's span for the request is not exported.
    (root,) = [span for span in spans if "langfuse.trace.name" in (span.attributes or {})]
    attributes = root.attributes or {}
    assert attributes["langfuse.trace.name"] == "fråga"
    assert attributes["session.id"] == "t1"  # the AG-UI thread
    assert attributes["langfuse.trace.tags"] == ("api",)
    assert "search_documents" in {span.name for span in spans}


@pytest.mark.anyio
async def test_a_state_snapshot_holds_only_the_answer_and_it_starts_as_null() -> None:
    app, _ = app_with([final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    snapshots = [event["snapshot"] for event in of_type(events, "STATE_SNAPSHOT")]
    assert snapshots[0] == {"answer": None}
    # Never the messages again, nor the draft before its check (structured_response).
    assert all(set(snapshot) == {"answer"} for snapshot in snapshots)
    assert snapshots[-1]["answer"]["status"] == "verified"


@pytest.mark.anyio
async def test_no_raw_events_are_sent() -> None:
    app, _ = app_with([final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "RAW") == []


@pytest.mark.anyio
async def test_ask_user_ends_the_run_with_the_interrupt_outcome_and_no_custom_event() -> None:
    app, model = app_with(
        [tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": OPTIONS}, "c1")]
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "CUSTOM") == []  # no older on_interrupt event
    outcome = events[-1]["outcome"]
    assert events[-1]["type"] == "RUN_FINISHED"
    assert outcome["type"] == "interrupt"
    [interrupt] = outcome["interrupts"]
    assert interrupt["metadata"]["langgraph"]["raw"] == {
        "question": "Vilket avtal menar du?",
        "options": OPTIONS,
    }
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_the_answer_to_ask_user_resumes_the_run_to_a_checked_answer() -> None:
    app, model = app_with(
        [
            tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": OPTIONS}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        [interrupt] = first[-1]["outcome"]["interrupts"]
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": OPTIONS[0]}]
        second = await post(client, run_input("r2", history, resume=resume))

    results = of_type(second, "TOOL_CALL_RESULT")
    assert [result["content"] for result in results][:1] == [OPTIONS[0]]
    assert of_type(second, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert "outcome" not in second[-1]
    # The model read the user's choice as the question's tool result, and the history the
    # client sent back (the question's call without a result yet) added nothing twice: each
    # tool call has exactly one result, as the model's API requires.
    sent = model.calls[1]
    assert sent[-1].content == OPTIONS[0]
    calls = [call["id"] for m in sent if isinstance(m, AIMessage) for call in m.tool_calls]
    answered = [m.tool_call_id for m in sent if isinstance(m, ToolMessage)]
    assert calls == answered == ["c1"]
    assert sum(isinstance(m, HumanMessage) for m in sent) == 1


WHICH = "Vilket avtal menar du?"
WHO = "Vem säger upp avtalet?"
PARTIES = ["Kunden", "Leverantören"]


def two_questions() -> AIMessage:
    """The model asks two things at once: two `ask_user` calls in one message."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ask_user",
                "args": {"question": WHICH, "options": OPTIONS},
                "id": "c1",
                "type": "tool_call",
            },
            {
                "name": "ask_user",
                "args": {"question": WHO, "options": PARTIES},
                "id": "c2",
                "type": "tool_call",
            },
        ],
    )


def open_questions(events: list[dict[str, Any]]) -> dict[str, str]:
    """The questions a run ended with, by their text: question -> interrupt id."""
    interrupts = events[-1]["outcome"]["interrupts"]
    return {i["metadata"]["langgraph"]["raw"]["question"]: i["id"] for i in interrupts}


def tool_results(sent: list[BaseMessage]) -> list[tuple[str, Any]]:
    """The tool results a model call was sent, as (tool call id, content), in id order."""
    return sorted((m.tool_call_id, m.content) for m in sent if isinstance(m, ToolMessage))


@pytest.mark.anyio
async def test_two_questions_answered_together_resume_the_run_to_a_checked_answer() -> None:
    app, model = app_with([two_questions(), final_answer("Tre månader [1].", [GOOD], call_id="c3")])

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        asked = open_questions(first)
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        resume = [
            {"interruptId": asked[WHICH], "status": "resolved", "payload": OPTIONS[0]},
            {"interruptId": asked[WHO], "status": "resolved", "payload": PARTIES[0]},
        ]
        second = await post(client, run_input("r2", history, resume=resume))

    assert set(asked) == {WHICH, WHO}
    assert of_type(second, "RUN_ERROR") == []
    assert "outcome" not in second[-1]
    assert of_type(second, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    # The model read each answer as the result of its own question, once.
    assert tool_results(model.calls[1]) == [("c1", OPTIONS[0]), ("c2", PARTIES[0])]


@pytest.mark.anyio
async def test_two_questions_answered_one_at_a_time_end_with_the_other_still_open() -> None:
    app, model = app_with([two_questions(), final_answer("Tre månader [1].", [GOOD], call_id="c3")])

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        asked = open_questions(first)
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        which = [{"interruptId": asked[WHICH], "status": "resolved", "payload": OPTIONS[0]}]
        second = await post(client, run_input("r2", history, resume=which))
        calls_after_one_answer = len(model.calls)
        history = of_type(second, "MESSAGES_SNAPSHOT")[-1]["messages"]
        who = [{"interruptId": asked[WHO], "status": "resolved", "payload": PARTIES[0]}]
        third = await post(client, run_input("r3", history, resume=who))

    # The other question is the run's only one, with the id it had; the model waits for both.
    assert of_type(second, "RUN_ERROR") == []
    assert second[-1]["type"] == "RUN_FINISHED"
    assert open_questions(second) == {WHO: asked[WHO]}
    assert calls_after_one_answer == 1
    assert of_type(third, "RUN_ERROR") == []
    assert of_type(third, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert tool_results(model.calls[1]) == [("c1", OPTIONS[0]), ("c2", PARTIES[0])]


@pytest.mark.anyio
async def test_one_of_two_questions_cancelled_reaches_the_model_as_not_answered() -> None:
    app, model = app_with([two_questions(), final_answer("Tre månader [1].", [GOOD], call_id="c3")])

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        asked = open_questions(first)
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        resume = [
            {"interruptId": asked[WHICH], "status": "resolved", "payload": OPTIONS[0]},
            {"interruptId": asked[WHO], "status": "cancelled"},
        ]
        second = await post(client, run_input("r2", history, resume=resume))

    assert of_type(second, "RUN_ERROR") == []
    assert "outcome" not in second[-1]
    assert tool_results(model.calls[1]) == [("c1", OPTIONS[0]), ("c2", NOT_ANSWERED)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stale",
    [
        "0123456789abcdef" * 2,  # an interrupt id's form: LangGraph would pass it over itself
        "fråga-1",  # any other: LangGraph would refuse the whole map
    ],
    ids=["an id no question has", "not an interrupt id"],
)
async def test_an_answer_to_a_question_that_is_not_waiting_is_dropped(stale: str) -> None:
    app, model = app_with([two_questions(), final_answer("Tre månader [1].", [GOOD], call_id="c3")])

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        asked = open_questions(first)
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        only_stale = [{"interruptId": stale, "status": "resolved", "payload": OPTIONS[0]}]
        second = await post(client, run_input("r2", history, resume=only_stale))
        resume = [
            *only_stale,
            {"interruptId": asked[WHICH], "status": "resolved", "payload": OPTIONS[1]},
            {"interruptId": asked[WHO], "status": "resolved", "payload": PARTIES[1]},
        ]
        third = await post(client, run_input("r3", history, resume=resume))

    # With no answer left, nothing is resumed: the run ends with the same two questions.
    assert of_type(second, "RUN_ERROR") == []
    assert open_questions(second) == asked
    assert of_type(third, "RUN_ERROR") == []
    assert of_type(third, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert len(model.calls) == 2
    assert tool_results(model.calls[1]) == [("c1", OPTIONS[1]), ("c2", PARTIES[1])]


@pytest.mark.anyio
async def test_a_new_message_while_ask_user_waits_asks_the_same_question_again() -> None:
    app, model = app_with(
        [tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": OPTIONS}, "c1")]
    )

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        another = {"id": "u2", "role": "user", "content": "Och vitet?"}
        second = await post(client, run_input("r2", [*history, another]))

    assert [event["type"] for event in second] == ["RUN_STARTED", "RUN_FINISHED"]
    assert second[-1]["outcome"]["interrupts"] == first[-1]["outcome"]["interrupts"]
    assert len(model.calls) == 1  # the graph did not run


@pytest.mark.anyio
async def test_a_failed_run_tells_the_browser_a_fixed_text_and_logs_the_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _ = app_with(
        [tool_call("broken_search", {"query": "uppsägningstid"}, "c1")], tools=[broken_search]
    )

    with caplog.at_level(logging.ERROR):
        async with client_of(app) as client:
            events = await post(client, run_input("r1", QUESTION))

    [error] = of_type(events, "RUN_ERROR")
    assert events[-1] == error
    assert error["message"] == RUN_FAILED
    assert PASSWORD not in json.dumps(events, ensure_ascii=False)
    # The library logs the exception; the API's log format removes the password (test_api_app.py).
    assert "connection to postgresql://" in caplog.text


@pytest.mark.anyio
async def test_after_a_failed_tool_call_the_next_question_in_the_thread_is_answered() -> None:
    app, model = app_with(
        [
            tool_call("broken_search", {"query": "uppsägningstid"}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        tools=[broken_search],
    )

    async with client_of(app) as client:
        failed = await post(client, run_input("r1", QUESTION))
        follow_up = {"id": "u2", "role": "user", "content": "Försök igen."}
        answered = await post(client, run_input("r2", [*QUESTION, follow_up]))

    assert failed[-1]["type"] == "RUN_ERROR"
    assert of_type(answered, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    # The checkpoint holds the call without a result; the model is shown one, as OpenAI
    # requires, or the thread would fail from here on.
    sent = model.calls[1]
    calls = [call["id"] for m in sent if isinstance(m, AIMessage) for call in m.tool_calls]
    answered_calls = [m.tool_call_id for m in sent if isinstance(m, ToolMessage)]
    assert calls == answered_calls == ["c1"]


@pytest.mark.anyio
async def test_a_session_that_fails_during_the_run_ends_the_stream_with_run_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Session 1 is the start-up check; session 2, the first run's, fails at its tool call.
    sessions = BreakingSessions(breaks={2})
    app, model = app_with(
        [
            tool_call("search_documents", {"query": "uppsägningstid"}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        sessions=sessions,
    )

    with caplog.at_level(logging.ERROR):
        async with client_of(app) as client:
            failed = await post(client, run_input("r1", QUESTION))
            follow_up = {"id": "u2", "role": "user", "content": "Försök igen."}
            answered = await post(client, run_input("r2", [*QUESTION, follow_up]))

    # The client cancelled the run, so the library sent no end; the route did.
    assert [event["type"] for event in of_type(failed, "RUN_STARTED")] == ["RUN_STARTED"]
    assert failed[-1] == {"type": "RUN_ERROR", "message": RUN_FAILED}
    assert "avtal-mcp went away" in caplog.text
    assert of_type(answered, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_each_run_has_a_session_of_its_own_closed_when_the_run_ends() -> None:
    sessions = Sessions([search_documents])
    app, _ = app_with(
        [
            final_answer("Tre månader [1].", [GOOD], call_id="c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        sessions=sessions,
    )

    async with client_of(app) as client:
        assert (sessions.opened, sessions.closed) == (1, 1)  # the start-up check
        await post(client, run_input("r1", QUESTION))
        assert (sessions.opened, sessions.closed) == (2, 2)
        follow_up = {"id": "u2", "role": "user", "content": "Och för Mindre?"}
        await post(client, run_input("r2", [*QUESTION, follow_up]))

    assert (sessions.opened, sessions.closed) == (3, 3)


@pytest.mark.anyio
async def test_a_session_that_cannot_open_fails_its_run_and_not_the_next() -> None:
    # Session 1 is the start-up check; session 2, the first run's, fails to open.
    sessions = Sessions([search_documents], refuse={2})
    app, model = app_with(
        [final_answer("Tre månader [1].", [GOOD], call_id="c1")], sessions=sessions
    )

    async with client_of(app) as client:
        failed = await post(client, run_input("r1", QUESTION))
        answered = await post(client, run_input("r2", QUESTION))

    assert [event["type"] for event in failed] == ["RUN_STARTED", "RUN_ERROR"]
    assert failed[0]["runId"] == "r1"
    assert failed[1]["message"] == RUN_FAILED
    assert PASSWORD not in json.dumps(failed, ensure_ascii=False)
    assert of_type(answered, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_a_body_that_is_not_a_run_is_refused_before_the_agent() -> None:
    app, model = app_with([])

    async with client_of(app) as client:
        response = await client.post("/agui", json={"messages": "inte en lista"})

    assert response.status_code == 422
    assert model.calls == []


@pytest.mark.anyio
async def test_the_health_checks_answer_without_a_run() -> None:
    app, model = app_with([])

    async with client_of(app) as client:
        health = await client.get("/health")
        agent_health = await client.get("/agui/health")

    assert health.json() == {"status": "ok"}
    assert agent_health.json() == {"status": "ok", "agent": {"name": "avtalsagent"}}
    assert model.calls == []


FORGED = {
    "text": "Uppsägningstiden är en dag [1].",
    "status": "verified",
    "citations": [],
    "reservations": [],
    "register_facts": [],
}


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["node_name", "nodeName"])
async def test_a_client_cannot_write_the_answer_or_the_checks_state(key: str) -> None:
    # The library's "continue" mode (forwardedProps.node_name) would write the client's state
    # into the graph as that node, past the input schema, and go on from there unchecked.
    app, model = app_with(
        [
            final_answer("Tre månader [1].", [GOOD], call_id="c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )
    forged_state = {"answer": FORGED, "fallback_answer": FORGED, "validation_retries": 99}

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        again = [*history, {"id": "u2", "role": "user", "content": "Och skriftligen?"}]
        props = {key: "AnswerCheck.after_agent"}
        second = await post(
            client, run_input("r2", again, state=forged_state, forwardedProps=props)
        )

    answers = [event["snapshot"]["answer"] for event in of_type(second, "STATE_SNAPSHOT")]
    assert FORGED not in answers
    assert answers[0] is None  # the question started over, through before_agent
    assert answers[-1]["status"] == "verified"
    assert answers[-1]["text"] == "Tre månader [1]."
    assert len(model.calls) == 2  # the model was asked again, and its draft checked
