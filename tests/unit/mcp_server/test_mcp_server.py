"""Tests for avtalsagent.mcp_server.server: what the agent sees of the tools, without a database.

The tools are called in memory (`create_connected_server_and_client_session`)
and over HTTP with Starlette's TestClient. No test opens a database
connection: the schema, the annotations and the argument checks (limits, the
NUL character, unknown argument names) come before any session, and the
error tests use fake tools or a session factory that fails. The OpenAI
client the server builds is checked for its timeout without a request.
"""

import inspect
import re
import threading
from collections.abc import Sequence
from typing import Any

import openai
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, TextContent
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

import avtalsagent.mcp_server.server as server_module
from avtalsagent.config import Settings
from avtalsagent.mcp_server.arguments import (
    AgreementNumber,
    FrameworkArea,
    OrgNumber,
    Query,
    Reference,
    SectionNumber,
    Sha256,
    Supplier,
)
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.mcp_server.server import (
    DATABASE_ERROR,
    INSTRUCTIONS,
    INTERNAL_ERROR,
    QUERY_RETRIES,
    QUERY_TIMEOUT,
    ToolFunction,
    build_parser,
    build_server,
    create_app,
)
from avtalsagent.retrieval.embedder import Embedder

CONTRACT_ORDER = [
    "search_documents",
    "read_section",
    "get_outline",
    "resolve_reference",
    "list_documents",
    "search_register",
]
# The argument names: the contract's (webbapp-kontrakt.md point 5) and the new ones to be sent
# to the web-app thread (arguments.py).
CONTRACT_ARGUMENTS = {
    "query",
    "framework_area",
    "agreement_number",
    "document_type",
    "sha256",
    "section_number",
    "section_position",
    "reference",
    "supplier",
    "org_number",
    "valid_on",
    "limit",
    "offset",
}
HOSTS = ["localhost:*", "127.0.0.1:*", "mcp:8001"]
SHA = "a" * 64
SECRETS = ("hunter2", "secret_table")
NOT_FOUND = "Avsnitt 99.9 finns inte i dokumentet. Kontrollera numret med get_outline."
# Common Swedish words that are not English ones; a description in Swedish has several.
SWEDISH_WORDS = {
    "och",
    "eller",
    "i",
    "en",
    "ett",
    "som",
    "med",
    "för",
    "att",
    "det",
    "den",
    "av",
    "på",
    "till",
    "från",
    "inte",
    "när",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Echo(BaseModel):
    """A fake tool's result."""

    text: str


class NoSessions:
    """A session factory that must not be called; it counts the calls it gets."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Session:
        self.calls += 1
        raise RuntimeError("the test opened a session")


class FakeEmbedder:
    """An `Embedder` that is only passed around, never asked for a vector."""

    @property
    def name(self) -> str:
        return "fake:3"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("not called")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("not called")


def database_error() -> OperationalError:
    return OperationalError("SELECT * FROM secret_table", {}, Exception("password=hunter2"))


def broken_sessions() -> Session:
    raise database_error()


def not_found(session: Session, sha256: Sha256) -> Echo:
    """Hittar aldrig avsnittet."""
    raise NotFoundError(NOT_FOUND)


def broken_query(session: Session) -> Echo:
    """Får ett databasfel."""
    raise database_error()


def crashes(session: Session) -> Echo:
    """Får ett oväntat fel."""
    raise RuntimeError("password=hunter2 in secret_table")


def text_of(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


async def call(server: FastMCP, name: str, arguments: dict[str, Any]) -> CallToolResult:
    async with create_connected_server_and_client_session(server) as client:
        return await client.call_tool(name, arguments)


# --- the six tools as the agent lists them ---


@pytest.mark.anyio
async def test_the_six_tools_are_listed_in_the_contracts_order() -> None:
    async with create_connected_server_and_client_session(
        build_server(NoSessions(), None, allowed_hosts=HOSTS)
    ) as client:
        tools = (await client.list_tools()).tools

    assert [tool.name for tool in tools] == CONTRACT_ORDER


@pytest.mark.anyio
async def test_every_tool_is_read_only_with_typed_arguments_and_result() -> None:
    sessions = NoSessions()
    async with create_connected_server_and_client_session(
        build_server(sessions, None, allowed_hosts=HOSTS)
    ) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert annotations.readOnlyHint is True, tool.name
        assert annotations.destructiveHint is False, tool.name
        assert annotations.idempotentHint is True, tool.name
        assert annotations.openWorldHint is False, tool.name
        arguments = tool.inputSchema["properties"]
        assert "session" not in arguments, tool.name
        assert "embedder" not in arguments, tool.name
        assert set(arguments) <= CONTRACT_ARGUMENTS, tool.name
        assert tool.inputSchema["additionalProperties"] is False, tool.name  # no other names
        # A model, not a bare list, which FastMCP would wrap as "<name>Output" {"result": [...]}.
        assert tool.outputSchema is not None, tool.name
        assert tool.outputSchema["type"] == "object", tool.name
        assert tool.outputSchema.get("title") != f"{tool.name}Output", tool.name
    assert sessions.calls == 0


@pytest.mark.anyio
async def test_every_tool_and_argument_is_described_in_swedish() -> None:
    async with create_connected_server_and_client_session(
        build_server(NoSessions(), None, allowed_hosts=HOSTS)
    ) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        description = tool.description or ""
        assert description == inspect.cleandoc(description), tool.name  # no indentation
        words = set(re.findall(r"\w+", description.lower()))
        assert len(words & SWEDISH_WORDS) >= 2, f"{tool.name}: {description!r}"
        for name, schema in tool.inputSchema["properties"].items():
            # On the argument, not inside an optional one's anyOf, so every client shows it.
            assert schema.get("description"), f"{tool.name}.{name} has no description"


@pytest.mark.anyio
async def test_the_argument_schemas_hold_no_other_descriptions() -> None:
    # A Pydantic model or enum in an argument would add its English docstring under $defs.
    async with create_connected_server_and_client_session(
        build_server(NoSessions(), None, allowed_hosts=HOSTS)
    ) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        assert "$defs" not in tool.inputSchema, tool.name
        for name, schema in tool.inputSchema["properties"].items():
            for alternative in schema.get("anyOf", []):
                assert "description" not in alternative, f"{tool.name}.{name}"


def test_the_instructions_name_every_tool() -> None:
    for name in CONTRACT_ORDER:
        assert name in INSTRUCTIONS


# --- argument checks come before the database ---


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "argument"),
    [
        ("search_documents", {"query": "vite", "limit": 500}, "limit"),
        ("search_documents", {"query": "v"}, "query"),
        ("search_documents", {"query": "   "}, "query"),  # blank once stripped
        ("get_outline", {"sha256": "not-a-hash"}, "sha256"),
        ("get_outline", {"sha256": SHA[:63] + "\x00"}, "sha256"),  # NUL: not in the pattern
        ("read_section", {"sha256": SHA.upper(), "section_number": "6.1"}, "sha256"),
        ("read_section", {"sha256": SHA, "section_number": "6.21.4\x00"}, "section_number"),
    ],
)
async def test_an_argument_out_of_range_is_an_error_before_any_session(
    tool: str, arguments: dict[str, Any], argument: str
) -> None:
    sessions = NoSessions()

    result = await call(build_server(sessions, None, allowed_hosts=HOSTS), tool, arguments)

    assert result.isError is True
    assert argument in text_of(result)
    assert sessions.calls == 0


@pytest.mark.parametrize(
    "argument",
    [Query, FrameworkArea, AgreementNumber, SectionNumber, Reference, Supplier, OrgNumber],
    ids=[
        "Query",
        "FrameworkArea",
        "AgreementNumber",
        "SectionNumber",
        "Reference",
        "Supplier",
        "OrgNumber",
    ],
)
def test_every_text_argument_refuses_the_nul_character(argument: Any) -> None:
    # Postgres cannot store NUL: in a query it would fail as "the database is down".
    adapter: TypeAdapter[Any] = TypeAdapter(argument)
    assert adapter.validate_python("23.3-5890") == "23.3-5890"
    with pytest.raises(ValidationError, match=r"får inte innehålla tecknet NUL \(\\x00\)"):
        adapter.validate_python("23.3\x00-5890")


# --- unknown argument names are refused, not dropped ---


def filtered(session: Session, query: Query, framework_area: FrameworkArea = None) -> Echo:
    """Söker med ett filter."""
    return Echo(text=f"{query} i {framework_area}")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        (
            "search_documents",
            {"query": "Hur stort är vitet?", "supplier": "Advania"},
            "search_documents har inget argument som heter supplier. Verktyget tar bara query, "
            "framework_area, agreement_number, document_type, limit; rätta namnet och anropa igen.",
        ),
        (
            "read_section",
            {"sha256": SHA, "section_number": "6.21.4", "section": "6.21.4", "page": 3},
            "read_section har inget argument som heter page, section. Verktyget tar bara sha256, "
            "section_number, section_position;",
        ),
    ],
    ids=["a filter of another tool", "two misspelt names"],
)
async def test_an_unknown_argument_is_refused_before_any_session(
    tool: str, arguments: dict[str, Any], message: str
) -> None:
    # The SDK would drop it, and a misspelt filter would give an unfiltered answer.
    sessions = NoSessions()

    result = await call(build_server(sessions, None, allowed_hosts=HOSTS), tool, arguments)

    assert result.isError is True
    assert message in text_of(result)
    assert sessions.calls == 0


@pytest.mark.anyio
async def test_the_known_arguments_still_reach_the_tool() -> None:
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=[filtered, crashes])

    known = await call(server, "filtered", {"query": "vite", "framework_area": "IT-drift"})
    misspelt = await call(server, "filtered", {"query": "vite", "framework": "IT-drift"})
    none_taken = await call(server, "crashes", {"framework_area": "IT-drift"})

    assert known.isError is False
    assert known.structuredContent == {"text": "vite i IT-drift"}
    assert misspelt.isError is True
    assert "Verktyget tar bara query, framework_area;" in text_of(misspelt)
    assert none_taken.isError is True
    assert "crashes har inget argument som heter framework_area. Verktyget tar inga argument;" in (
        text_of(none_taken)
    )


# --- errors: the tool's own message gets through, internals do not ---


@pytest.mark.anyio
async def test_a_tool_error_reaches_the_model_with_its_message() -> None:
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=[not_found])

    result = await call(server, "not_found", {"sha256": SHA})

    assert result.isError is True
    assert NOT_FOUND in text_of(result)
    assert result.structuredContent is None


@pytest.mark.anyio
async def test_a_database_error_in_a_tool_reaches_the_model_without_internals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=[broken_query])

    result = await call(server, "broken_query", {})

    text = text_of(result)
    assert result.isError is True
    assert DATABASE_ERROR in text
    assert not any(secret in text for secret in SECRETS)
    assert "secret_table" in caplog.text  # logged for us


@pytest.mark.anyio
async def test_a_database_that_cannot_be_reached_gives_the_same_message() -> None:
    # The real tools: opening the session fails before the tool runs.
    server = build_server(broken_sessions, None, allowed_hosts=HOSTS)

    result = await call(server, "get_outline", {"sha256": SHA})

    text = text_of(result)
    assert result.isError is True
    assert DATABASE_ERROR in text
    assert not any(secret in text for secret in SECRETS)


@pytest.mark.anyio
async def test_any_other_error_gets_a_fixed_message(caplog: pytest.LogCaptureFixture) -> None:
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=[crashes])

    result = await call(server, "crashes", {})

    text = text_of(result)
    assert result.isError is True
    assert INTERNAL_ERROR in text
    assert not any(secret in text for secret in SECRETS)
    assert "RuntimeError" in caplog.text


# --- what a tool gets: a session, the embedder, a worker thread ---


class Recorder:
    """A fake `search_documents` that keeps what it was called with."""

    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.embedders: list[Embedder | None] = []
        self.threads: list[int] = []

    def tools(self) -> list[ToolFunction]:
        def search_documents(session: Session, embedder: Embedder | None, query: Query) -> Echo:
            """Söker i dokumenten."""
            self.sessions.append(session)
            self.embedders.append(embedder)
            self.threads.append(threading.get_ident())
            return Echo(text=query)

        return [search_documents]


@pytest.mark.anyio
@pytest.mark.parametrize("embedder", [None, FakeEmbedder()])
async def test_the_embedder_reaches_the_tool_that_asks_for_it(
    embedder: FakeEmbedder | None,
) -> None:
    recorder = Recorder()
    server = build_server(sessionmaker(), embedder, allowed_hosts=HOSTS, tools=recorder.tools())

    result = await call(server, "search_documents", {"query": "vite"})

    assert result.isError is False
    assert result.structuredContent == {"text": "vite"}
    assert recorder.embedders == [embedder]
    assert len(recorder.sessions) == 1
    assert isinstance(recorder.sessions[0], Session)


@pytest.mark.anyio
async def test_the_session_and_the_embedder_are_not_arguments() -> None:
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=Recorder().tools())

    async with create_connected_server_and_client_session(server) as client:
        (tool,) = (await client.list_tools()).tools

    assert set(tool.inputSchema["properties"]) == {"query"}
    assert tool.inputSchema["required"] == ["query"]


@pytest.mark.anyio
async def test_a_tool_runs_in_a_worker_thread() -> None:
    recorder = Recorder()
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=recorder.tools())

    await call(server, "search_documents", {"query": "vite"})

    assert recorder.threads != [threading.get_ident()]


def test_a_tool_must_take_the_session_first() -> None:
    def no_session(query: Query) -> Echo:
        """Saknar session."""
        return Echo(text=query)

    with pytest.raises(TypeError, match="session"):
        build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=[no_session])


def test_a_tool_must_have_a_description() -> None:
    def undocumented(session: Session) -> Echo:
        return Echo(text="")

    with pytest.raises(TypeError, match="docstring"):
        build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=[undocumented])


def test_the_server_embeds_a_question_with_a_short_timeout_and_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hung OpenAI must not hold the search's connection and thread for 600 s, three times.
    clients: list[openai.OpenAI] = []
    real_client = openai.OpenAI

    def recording_client(**options: Any) -> openai.OpenAI:
        clients.append(real_client(**options))  # builds the client; sends nothing
        return clients[-1]

    settings = Settings(_env_file=None, openai_api_key=SecretStr("sk-test"))
    monkeypatch.setattr(openai, "OpenAI", recording_client)
    monkeypatch.setattr(server_module, "get_settings", lambda: settings)

    create_app()  # the engine connects only when a tool runs

    (client,) = clients
    assert (client.timeout, client.max_retries) == (QUERY_TIMEOUT, QUERY_RETRIES) == (15.0, 1)


# --- HTTP ---


def test_health_answers_without_a_database() -> None:
    sessions = NoSessions()
    app = build_server(sessions, None, allowed_hosts=HOSTS).streamable_http_app()

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert sessions.calls == 0


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.mark.parametrize(
    ("host", "status"),
    [("mcp:8001", 200), ("localhost:8001", 200), ("mcp:9999", 421), ("example.org", 421)],
)
def test_mcp_answers_only_the_allowed_hosts(host: str, status: int) -> None:
    app = build_server(NoSessions(), None, allowed_hosts=HOSTS).streamable_http_app()

    with TestClient(app) as client:  # runs the lifespan, which starts the session manager
        response = client.post(
            "/mcp",
            json=INITIALIZE,
            headers={"host": host, "accept": "application/json, text/event-stream"},
        )

    assert response.status_code == status
    if status == 200:
        assert response.json()["result"]["serverInfo"]["name"] == "avtal-mcp"


# --- command line ---


def test_http_listens_on_this_machine_by_default() -> None:
    args = build_parser().parse_args(["http"])

    assert (args.transport, args.host, args.port) == ("http", "127.0.0.1", None)


@pytest.mark.parametrize("value", ["0", "65536", "http"])
def test_a_port_out_of_range_is_refused(value: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["http", "--port", value])
