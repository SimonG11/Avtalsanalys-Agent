"""Tests for avtalsagent.api: the agent over AG-UI, run through the whole app.

Each test starts the app with its lifespan (`create_app` with a scripted
model, a search tool and sections in memory, and an in-memory checkpointer)
and posts AG-UI's `RunAgentInput` to `/agui` through httpx's ASGI transport,
reading the server-sent events as the web app does. No network, key or
database is used.
"""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import anyio
import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.sections import CitedSection
from avtalsagent.api.agui import RUN_FAILED
from avtalsagent.api.app import create_app
from avtalsagent.api.documents import DocumentFiles, StoredFile
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import DictReader, ScriptedModel, final_answer, tool_call

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
            yield McpTools(tools=self.tools, reader=DictReader([SECTION]))
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
                yield McpTools(tools=tools, reader=DictReader([SECTION]))
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
) -> tuple[FastAPI, ScriptedModel]:
    model = ScriptedModel(script=script)
    settings = Settings(
        _env_file=None, database_url=f"postgresql+psycopg://avtalsagent:{PASSWORD}@db:5432/x"
    )
    open_tools = (sessions or Sessions(tools or [search_documents])).open

    @asynccontextmanager
    async def open_saver(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
        yield InMemorySaver(serde=serializer())

    @contextmanager
    def open_documents(settings: Settings) -> Iterator[DocumentFiles]:
        yield NoDocuments()

    app = create_app(
        settings,
        make_model=lambda settings: model,
        open_tools=open_tools,
        open_saver=open_saver,
        open_documents=open_documents,
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
    }


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
