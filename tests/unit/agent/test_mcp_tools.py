"""Tests for avtalsagent.agent.mcp_tools: the agent's tools and section reader from avtal-mcp.

The real M6 server object is built with stand-ins for the six tools: each
has the real tool's name, signature and docstring (so the same schemas and
Swedish descriptions), and only `read_section` answers. The client side
runs in memory (`create_connected_server_and_client_session`), over HTTP
with uvicorn on this machine, and over stdio with the real server process;
no test opens a database connection or calls OpenAI.
"""

import functools
import inspect
import json
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import uvicorn
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.agent.mcp_tools import (
    McpSectionReader,
    McpTools,
    load_tools,
    open_mcp_tools,
    stdio_parameters,
)
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.mcp_server.references import SectionReference, TargetRef
from avtalsagent.mcp_server.server import DATABASE_ERROR, ToolFunction, build_server
from avtalsagent.mcp_server.tools import TOOLS
from avtalsagent.mcp_server.tools.read_section import Section

CONTRACT_ORDER = [
    "search_documents",
    "read_section",
    "get_outline",
    "resolve_reference",
    "list_documents",
    "search_register",
]
HOSTS = ["localhost:*", "127.0.0.1:*", "mcp:8001"]
SHA = "ab" * 32
OTHER_SHA = "cd" * 32
FAKE_KEY = "sk-test-not-a-real-key"
NOT_FOUND = "Det finns inget avsnitt på plats {position} i dokumentet {sha}…. Se get_outline."

SECTION = Section(
    sha256=SHA,
    file_title="Allmänna villkor",
    document_type="general_terms",
    page_titles=["IT-drift Större, fler än 200 anställda", "IT-drift Mindre"],
    section_position=42,
    section_number="6.21.9",
    section_title="Uppsägning",
    page_start=14,
    page_end=15,
    path=["6 Allmänna villkor", "6.21 Avtalets upphörande", "6.21.9 Uppsägning"],
    text="Leverantören får säga upp Kontraktet med en uppsägningstid om tre (3) månader.",
    references=[
        SectionReference(
            raw="punkt 6.21.4",
            kind="section_number",
            status="resolved",
            targets=[
                TargetRef(
                    sha256=SHA,
                    file_title="Allmänna villkor",
                    document_type="general_terms",
                    page_titles=["IT-drift Större, fler än 200 anställda"],
                    section_position=37,
                    section_number="6.21.4",
                    section_title="Hävning",
                    page_start=13,
                    page_end=13,
                )
            ],
            held_back_targets=0,
        )
    ],
    held_back_targets=1,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- the M6 server with the real tools' schemas and a read_section that answers ---


def answer_read_section(
    sha256: str, section_number: str | None = None, section_position: int | None = None
) -> Section:
    if (sha256, section_position) == (SHA, SECTION.section_position):
        return SECTION
    raise NotFoundError(NOT_FOUND.format(position=section_position, sha=sha256[:12]))


def stand_in(real: ToolFunction, answer: Callable[..., BaseModel] | None) -> ToolFunction:
    """The real tool's name, docstring and signature (`__wrapped__`), with `answer` as its body."""

    @functools.wraps(real)
    def tool(session: Session, *embedder: Any, **arguments: Any) -> BaseModel:
        if answer is None:
            raise AssertionError(f"the test called {real.__name__}")
        return answer(**arguments)

    return tool


def stand_in_server() -> FastMCP:
    answers = {"read_section": answer_read_section}
    tools = [stand_in(real, answers.get(real.__name__)) for real in TOOLS]
    return build_server(sessionmaker(), None, allowed_hosts=HOSTS, tools=tools)


@pytest.fixture
async def mcp_tools() -> AsyncIterator[McpTools]:
    async with create_connected_server_and_client_session(stand_in_server()) as session:
        yield await load_tools(session)


def by_name(tools: list[BaseTool], name: str) -> BaseTool:
    return next(tool for tool in tools if tool.name == name)


async def call(tool: BaseTool, arguments: dict[str, Any]) -> ToolMessage:
    """Call a tool as the agent's tool node does: with a tool call, giving a ToolMessage."""
    message = await tool.ainvoke(
        {"name": tool.name, "args": arguments, "id": "call-1", "type": "tool_call"}
    )
    assert isinstance(message, ToolMessage)
    return message


def text_of(message: ToolMessage) -> str:
    assert isinstance(message.content, list)
    return "".join(block["text"] for block in message.content if isinstance(block, dict))


# --- the tools ---


@pytest.mark.anyio
async def test_the_tools_are_avtal_mcps_six_in_the_contracts_order(mcp_tools: McpTools) -> None:
    assert [tool.name for tool in mcp_tools.tools] == CONTRACT_ORDER


@pytest.mark.anyio
async def test_each_tool_has_its_swedish_description_and_closed_schema(
    mcp_tools: McpTools,
) -> None:
    for real, tool in zip(TOOLS, mcp_tools.tools, strict=True):
        assert real.__doc__ is not None
        assert tool.description == inspect.cleandoc(real.__doc__), tool.name
        assert isinstance(tool.args_schema, dict)
        # The model sees the server's schema: no other argument names, descriptions included.
        assert tool.args_schema["additionalProperties"] is False, tool.name
        assert "session" not in tool.args_schema["properties"], tool.name
        assert tool.metadata is not None
        assert tool.metadata["readOnlyHint"] is True, tool.name


@pytest.mark.anyio
async def test_a_tool_result_reaches_the_model_as_json_text_with_the_structure_beside_it(
    mcp_tools: McpTools,
) -> None:
    message = await call(
        by_name(mcp_tools.tools, "read_section"),
        {"sha256": SHA, "section_position": SECTION.section_position},
    )

    expected = SECTION.model_dump(mode="json")
    assert message.status == "success"
    assert json.loads(text_of(message)) == expected  # what the model reads
    assert message.artifact == {"structured_content": expected}  # not sent to the model


@pytest.mark.anyio
async def test_a_tool_error_reaches_the_model_as_an_error_message_and_the_run_goes_on(
    mcp_tools: McpTools,
) -> None:
    message = await call(
        by_name(mcp_tools.tools, "read_section"), {"sha256": SHA, "section_position": 7}
    )

    assert message.status == "error"
    assert NOT_FOUND.format(position=7, sha=SHA[:12]) in text_of(message)


# --- the section reader ---


@pytest.mark.anyio
async def test_the_reader_gives_the_section_with_its_citation_fields(mcp_tools: McpTools) -> None:
    section = await mcp_tools.reader.read(SHA, SECTION.section_position)

    assert section == CitedSection(
        sha256=SHA,
        section_position=42,
        section_number="6.21.9",
        section_title="Uppsägning",
        file_title="Allmänna villkor",
        page_titles=["IT-drift Större, fler än 200 anställda", "IT-drift Mindre"],
        page_start=14,
        text=SECTION.text,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sha256", "position"),
    [(SHA, 7), (OTHER_SHA, 42), ("not-a-hash", 42), (SHA, -1)],
    ids=["no such section", "no such file", "a malformed hash", "a negative position"],
)
async def test_the_reader_gives_none_for_a_section_the_server_does_not_give(
    mcp_tools: McpTools, sha256: str, position: int
) -> None:
    assert await mcp_tools.reader.read(sha256, position) is None


@pytest.mark.anyio
async def test_the_reader_is_an_mcp_section_reader(mcp_tools: McpTools) -> None:
    assert isinstance(mcp_tools.reader, McpSectionReader)


# --- the transports ---


def test_stdio_starts_the_server_with_this_interpreter_and_the_whole_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/avtalsagent")

    parameters = stdio_parameters()

    assert parameters.command == sys.executable
    assert parameters.args == ["-m", "avtalsagent.mcp_server", "stdio"]
    assert parameters.env is not None
    assert sorted(parameters.env) == sorted(os.environ)  # names only: values may be secret
    assert parameters.env["OPENAI_API_KEY"] == FAKE_KEY
    assert parameters.env["DATABASE_URL"] == "postgresql+psycopg://u:p@db:5432/avtalsagent"


@pytest.mark.anyio
async def test_stdio_runs_the_real_server_with_the_agents_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing listens on port 9, so the database fails at once. With the key in the server's
    # environment the search gets as far as the database; without it, it would say the key
    # is missing.
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://avtalsagent:x@127.0.0.1:9/none")
    settings = Settings(_env_file=None, mcp_transport="stdio")

    async with open_mcp_tools(settings) as mcp_tools:
        names = [tool.name for tool in mcp_tools.tools]
        search = await call(mcp_tools.tools[0], {"query": "uppsägningstid"})

    assert names == CONTRACT_ORDER
    assert search.status == "error"
    assert DATABASE_ERROR in text_of(search)


@contextmanager
def serving(server: FastMCP) -> Iterator[str]:
    """The server's HTTP app on a free port of this machine; yields its MCP URL."""
    config = uvicorn.Config(
        server.streamable_http_app(), host="127.0.0.1", port=0, log_level="warning"
    )
    http = uvicorn.Server(config)
    thread = threading.Thread(target=http.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not http.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    port = http.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        http.should_exit = True
        thread.join(timeout=10)


@pytest.mark.anyio
async def test_streamable_http_connects_to_mcp_url() -> None:
    with serving(stand_in_server()) as url:
        settings = Settings(_env_file=None, mcp_transport="streamable_http", mcp_url=url)
        async with open_mcp_tools(settings) as mcp_tools:
            names = [tool.name for tool in mcp_tools.tools]
            section = await mcp_tools.reader.read(SHA, SECTION.section_position)
            missing = await mcp_tools.reader.read(SHA, 7)

    assert names == CONTRACT_ORDER
    assert section is not None
    assert section.text == SECTION.text
    assert missing is None


def test_the_transport_is_stdio_or_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    assert Settings(_env_file=None).mcp_transport == "stdio"
    monkeypatch.setenv("MCP_TRANSPORT", "streamable_http")
    assert Settings(_env_file=None).mcp_transport == "streamable_http"
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    with pytest.raises(ValueError, match="mcp_transport"):
        Settings(_env_file=None)
