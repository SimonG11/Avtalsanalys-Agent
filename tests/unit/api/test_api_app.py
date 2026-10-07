"""Tests for avtalsagent.api.app and api.__main__: start-up, shutdown, settings and the log.

The lifespan is run with stand-ins that record when they open and close;
`main` with uvicorn replaced, so no server or port is opened.
"""

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.model import MissingApiKeyError, make_agent_model
from avtalsagent.api import __main__ as api_main
from avtalsagent.api import app as api_app
from avtalsagent.api.agui import AgentRuns, AvtalAguiAgent
from avtalsagent.api.app import create_app, open_document_files
from avtalsagent.api.documents import DatabaseDocumentFiles, DocumentFiles, StoredFile
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
)

KEY = "sk-test-0123456789abcdef"
PASSWORD = "hemligt-lösen-42"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Recorder:
    """Stand-ins for the app's connections that record when each opens and closes."""

    def __init__(self) -> None:
        self.log: list[str] = []

    @asynccontextmanager
    async def open_tools(self, settings: Settings) -> AsyncIterator[McpTools]:
        self.log.append("open mcp")
        try:
            yield McpTools(
                tools=[],
                reader=DictReader([]),
                register=ListRegister(),
                amendments=DictAmendments(),
            )
        finally:
            self.log.append("close mcp")

    @asynccontextmanager
    async def open_saver(self, settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
        self.log.append("open checkpointer")
        try:
            yield InMemorySaver()
        finally:
            self.log.append("close checkpointer")

    @contextmanager
    def open_documents(self, settings: Settings) -> Iterator[DocumentFiles]:
        self.log.append("open documents")
        try:
            yield NoFiles()
        finally:
            self.log.append("close documents")


class NoFiles:
    def find(self, sha256: str) -> StoredFile | None:
        return None


def recorded_app(recorder: Recorder, settings: Settings, **more: Any) -> FastAPI:
    options: dict[str, Any] = {
        "make_model": lambda settings: ScriptedModel(script=[]),
        "make_answer_reviewer": lambda settings: ScriptedReviewer(),
        "open_tools": recorder.open_tools,
        "open_saver": recorder.open_saver,
        "open_documents": recorder.open_documents,
        **more,
    }
    return create_app(settings, **options)


@pytest.mark.anyio
async def test_the_app_checks_avtal_mcp_then_opens_its_connections_and_closes_them() -> None:
    recorder = Recorder()
    app = recorded_app(recorder, Settings(_env_file=None))

    async with app.router.lifespan_context(app):
        # avtal-mcp is asked once at start-up; each run opens a session of its own.
        assert recorder.log == ["open mcp", "close mcp", "open checkpointer", "open documents"]
        assert isinstance(app.state.runs, AgentRuns)
        assert isinstance(app.state.documents, NoFiles)
        async with app.state.runs.open() as agent:
            assert isinstance(agent, AvtalAguiAgent)
            assert recorder.log[4:] == ["open mcp"]
        assert recorder.log[5:] == ["close mcp"]

    assert recorder.log[6:] == ["close documents", "close checkpointer"]


@pytest.mark.anyio
async def test_an_avtal_mcp_that_cannot_be_reached_stops_the_start() -> None:
    recorder = Recorder()

    @asynccontextmanager
    async def unreachable(settings: Settings) -> AsyncIterator[McpTools]:
        raise OSError("All connection attempts failed")
        yield McpTools(  # pragma: no cover
            tools=[], reader=DictReader([]), register=ListRegister(), amendments=DictAmendments()
        )

    app = recorded_app(recorder, Settings(_env_file=None), open_tools=unreachable)

    with pytest.raises(OSError, match="connection attempts"):
        async with app.router.lifespan_context(app):
            pass

    assert recorder.log == []


@pytest.mark.anyio
async def test_without_a_key_the_app_stops_before_it_opens_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    recorder = Recorder()
    # The real model factory, and no key.
    settings = Settings(_env_file=None, openai_api_key=None)
    app = recorded_app(recorder, settings, make_model=make_agent_model)

    with pytest.raises(MissingApiKeyError):
        async with app.router.lifespan_context(app):
            pass

    assert recorder.log == []


@pytest.mark.anyio
async def test_a_connection_that_fails_to_open_closes_those_already_open() -> None:
    recorder = Recorder()

    @contextmanager
    def unreachable(settings: Settings) -> Iterator[DocumentFiles]:
        raise OSError("the database cannot be reached")
        yield NoFiles()  # pragma: no cover

    app = recorded_app(recorder, Settings(_env_file=None), open_documents=unreachable)

    with pytest.raises(OSError, match="cannot be reached"):
        async with app.router.lifespan_context(app):
            pass

    # The checkpointer, opened before the document lookup, is closed again.
    assert recorder.log == ["open mcp", "close mcp", "open checkpointer", "close checkpointer"]


@pytest.mark.anyio
async def test_a_checkpointer_that_cannot_open_stops_the_start() -> None:
    recorder = Recorder()

    @asynccontextmanager
    async def unreachable(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
        raise OSError("the database cannot be reached")
        yield InMemorySaver()  # pragma: no cover

    app = recorded_app(recorder, Settings(_env_file=None), open_saver=unreachable)

    with pytest.raises(OSError, match="cannot be reached"):
        async with app.router.lifespan_context(app):
            pass

    assert recorder.log == ["open mcp", "close mcp"]  # only the start-up check


def test_the_routes_are_part_of_the_app_before_it_starts() -> None:
    app = recorded_app(Recorder(), Settings(_env_file=None))

    paths = app.openapi()["paths"]

    assert set(paths) == {"/agui", "/agui/health", "/api/documents/{sha256}/pdf", "/health"}
    assert set(paths["/agui"]) == {"post"}


def test_the_document_lookup_reads_through_a_read_only_engine_it_disposes_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[tuple[str, bool]] = []
    disposed: list[bool] = []

    class StandInEngine:
        def dispose(self) -> None:
            disposed.append(True)

    def create_db_engine(url: str, *, read_only: bool) -> StandInEngine:
        made.append((url, read_only))
        return StandInEngine()

    monkeypatch.setattr(api_app, "create_db_engine", create_db_engine)
    monkeypatch.setattr(api_app, "session_factory", lambda engine: lambda: None)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@db:5432/x")

    with open_document_files(settings) as files:
        assert isinstance(files, DatabaseDocumentFiles)
        assert disposed == []

    assert made == [("postgresql+psycopg://u:p@db:5432/x", True)]
    assert disposed == [True]


# --- the command line and the log ----------------------------------------------------


def test_main_serves_the_app_at_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, Any] = {}

    def run(app: FastAPI, **options: Any) -> None:
        served["app"] = app
        served.update(options)

    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "8080")
    monkeypatch.setattr(api_main, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(uvicorn, "run", run)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        api_main.main()
        configured = root.handlers[:]
    finally:
        root.handlers, root.level = handlers, level

    assert isinstance(served["app"], FastAPI)
    assert served["host"] == "0.0.0.0"
    assert served["port"] == 8080
    assert served["log_config"] is None  # uvicorn logs through the root logger's handler
    [handler] = configured
    assert isinstance(handler.formatter, api_main.RedactingFormatter)


def test_the_api_listens_on_this_machine_at_port_8000_by_default() -> None:
    settings = Settings(_env_file=None)

    assert (settings.api_host, settings.api_port) == ("127.0.0.1", 8000)


def test_the_log_never_shows_the_key_or_the_password_not_even_in_a_traceback() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr(KEY),
        database_url=f"postgresql+psycopg://avtalsagent:{PASSWORD}@db:5432/x",
    )
    formatter = api_main.RedactingFormatter(settings)
    try:
        raise RuntimeError(f"connection to postgresql://avtalsagent:{PASSWORD}@db failed")
    except RuntimeError as error:
        record = logging.LogRecord(
            "ag_ui_langgraph.agent",
            logging.ERROR,
            __file__,
            1,
            "LangGraph run failed with key %s",
            (KEY,),
            (type(error), error, error.__traceback__),
        )

    line = formatter.format(record)

    assert KEY not in line
    assert PASSWORD not in line
    assert "postgresql://avtalsagent:***@db failed" in line
    assert "LangGraph run failed with key ***" in line
    assert "Traceback" in line
