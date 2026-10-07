"""Tests for list_documents and search_register: the checks that come before any query.

Both tools need at least one filter, `search_register` reads an
organisation number as the register stores it, `list_documents` says so
when there is no search index, and the server checks each limit, offset,
date and name before a session is opened. The tests pass a session, or a
session factory, that fails on any use, or one that only has no index.
What the tools read is tested on Postgres in
tests/integration/test_mcp_register.py.
"""

from datetime import date
from typing import Any, cast

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from sqlalchemy import Select
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.domain.extracted import DOCUMENT_GROUPS, DocumentType
from avtalsagent.mcp_server.errors import MissingArgumentError, NotFoundError, UnavailableError
from avtalsagent.mcp_server.server import build_server
from avtalsagent.mcp_server.tools.list_documents import GROUP_ORDER, list_documents
from avtalsagent.mcp_server.tools.search_register import register_org_number, search_register

HOSTS = ["localhost:*"]


class NoDatabase:
    """Stands in for a Session; any use of it fails the test."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the tool used the database (session.{name})")


NO_DATABASE = cast(Session, NoDatabase())


class NoIndex(NoDatabase):
    """Stands in for a Session on a database without a search index (no `index_build` row)."""

    def scalar(self, statement: Select[Any]) -> None:
        assert "FROM index_build" in str(statement), statement
        return None


class NoSessions:
    """A session factory that must not be called; it counts the calls it gets."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Session:
        self.calls += 1
        raise RuntimeError("the test opened a session")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def text_of(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


# --- list_documents ---


def test_list_documents_needs_a_filter_and_says_where_to_find_one() -> None:
    message = (
        "Ange minst ett av framework_area, agreement_number och document_type.* search_register"
    )
    with pytest.raises(MissingArgumentError, match=message):
        list_documents(NO_DATABASE)
    with pytest.raises(MissingArgumentError, match=message):
        list_documents(NO_DATABASE, limit=10)


def test_list_documents_without_an_index_says_so_instead_of_listing_nothing() -> None:
    # Between `process` and `index`: an empty list would read as "the agreement has no documents".
    with pytest.raises(
        UnavailableError, match="Sökindexet är inte byggt, så dokumenten kan inte listas"
    ):
        list_documents(cast(Session, NoIndex()), agreement_number="23.3-5890-2023-003")


def test_every_document_type_has_a_place_in_the_order() -> None:
    assert {DOCUMENT_GROUPS[document_type] for document_type in DocumentType} <= set(GROUP_ORDER)


# --- search_register ---


@pytest.mark.parametrize(
    "arguments",
    [{}, {"valid_on": date(2026, 10, 6)}, {"supplier": "   "}, {"limit": 5}],
    ids=["nothing", "only a date", "a blank name", "only a limit"],
)
def test_search_register_needs_a_filter_besides_the_date(arguments: dict[str, Any]) -> None:
    message = "Ange minst ett av supplier, agreement_number, framework_area och org_number"
    with pytest.raises(MissingArgumentError, match=message):
        search_register(NO_DATABASE, **arguments)


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("556214-9996", "556214-9996"),
        ("5562149996", "556214-9996"),
        (" 556214 9996 ", "556214-9996"),
        ("SE556214999601", "556214-9996"),  # the VAT number
        ("fi 01148912", "FI01148912"),  # a foreign number: spaces out, letters upper-cased
    ],
)
def test_an_org_number_is_read_as_the_register_stores_it(written: str, stored: str) -> None:
    assert register_org_number(written) == stored


def test_an_unreadable_org_number_is_an_error_before_the_database() -> None:
    with pytest.raises(NotFoundError, match="'55-62-14' kan inte läsas.* supplier"):
        search_register(NO_DATABASE, org_number="55-62-14")


# --- through MCP ---


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "argument"),
    [
        ("list_documents", {"document_type": "general_terms", "limit": 201}, "limit"),
        ("list_documents", {"document_type": "terms"}, "document_type"),
        ("list_documents", {"agreement_number": "23"}, "agreement_number"),
        ("search_register", {"supplier": "Advania", "limit": 21}, "limit"),
        ("search_register", {"supplier": "Advania", "valid_on": "2026-13-01"}, "valid_on"),
        ("search_register", {"supplier": "A"}, "supplier"),
        ("search_register", {"supplier": "Advania\x00"}, "supplier"),  # Postgres refuses NUL
        ("search_register", {"supplier": "Advania", "offset": -1}, "offset"),
    ],
)
async def test_an_argument_out_of_range_is_an_error_before_any_session(
    tool: str, arguments: dict[str, Any], argument: str
) -> None:
    sessions = NoSessions()
    server = build_server(sessions, None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(tool, arguments)

    assert result.isError is True
    assert argument in text_of(result)
    assert sessions.calls == 0


@pytest.mark.anyio
async def test_the_limits_and_their_defaults_are_in_the_schema() -> None:
    server = build_server(NoSessions(), None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    documents = tools["list_documents"].inputSchema["properties"]["limit"]
    register = tools["search_register"].inputSchema["properties"]["limit"]
    offset = tools["search_register"].inputSchema["properties"]["offset"]
    assert (documents["default"], documents["minimum"], documents["maximum"]) == (50, 1, 200)
    assert (register["default"], register["minimum"], register["maximum"]) == (20, 1, 20)
    assert (offset["default"], offset["minimum"], "maximum" in offset) == (0, 0, False)
    assert "total" in offset["description"]  # how to page
    assert "required" not in tools["search_register"].inputSchema  # one of several, checked inside


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("list_documents", {}, "Ange minst ett av framework_area"),
        ("search_register", {"valid_on": "2026-10-06"}, "Ange minst ett av supplier"),
    ],
)
async def test_a_missing_filter_reaches_the_model_as_its_message(
    tool: str, arguments: dict[str, Any], message: str
) -> None:
    # An unbound sessionmaker: the session is opened, but no query reaches a database.
    server = build_server(sessionmaker(), None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(tool, arguments)

    assert result.isError is True
    assert message in text_of(result)
    assert result.structuredContent is None
