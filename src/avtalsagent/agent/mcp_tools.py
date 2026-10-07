"""The agent's connection to avtal-mcp: the tools, and the readers the answer check uses.

What:
    `open_mcp_tools(settings)` opens one MCP session to avtal-mcp and yields
    `McpTools`: the server's tools as LangChain tools, in the server's order
    (`tools`), an `McpSectionReader` (`reader`), an `McpRegisterReader`
    (`register`) and an `McpAmendmentReader` (`amendments`) on the same
    session. `open_mcp_session` opens the session MCP_TRANSPORT names, and
    `load_tools` makes `McpTools` of a session that is already open.

Why:
    The agent gets its data only through avtal-mcp (ADR 0003), and the
    answer check reads each cited section, its amendments and each declared
    agreement from the server itself, never from the message history, which
    a client sends and could change. One session lasts as long as the agent: the adapter's
    `get_tools()` would open a new session for every tool call, which over
    stdio is a new server process each time.

How:
    stdio starts `python -m avtalsagent.mcp_server stdio` with the
    interpreter that runs the agent and the agent's whole environment; the
    MCP SDK would otherwise pass on only a few variables (PATH, HOME and
    such), and DATABASE_URL and OPENAI_API_KEY set in the shell would not
    reach the server. The environment goes straight to the SDK's
    `stdio_client`, not through the adapter's connection settings, which
    expand `${NAME}` in every value and log, value included, one they cannot
    expand: a password may hold such text. streamable_http connects to
    MCP_URL with the adapter's `MultiServerMCPClient.session`.
    `load_mcp_tools` turns the server's tool list into LangChain tools; a
    tool's error result (the server's Swedish `ToolError` text) comes back
    as a `ToolMessage` with status "error" that the model reads, and the run
    goes on (the adapter's `handle_tool_errors`, on by default).
    `McpSectionReader.read` calls `read_section` with the hash and the
    position, and validates the result's `structuredContent` (avtal-mcp's
    `Section`) into a `CitedSection`; an error result is None.
    `McpRegisterReader.read` calls `search_register` with the agreement
    number, 20 rows at a time (the tool's largest `limit`), with `offset`
    until `total` rows are read, and validates each row into a
    `RegisterEntry`. It keeps the rows whose agreement number has the
    requested one's key, as the server matches spellings (-001 and -01 are
    one agreement): for a number the register has only as a procurement,
    the server gives that procurement's agreements instead, and those are
    not the agreement asked for, so a page without a row of it ends the
    reading. No row of the agreement, or an error result, is None.
    `McpAmendmentReader.read` calls `find_amendments` with the hash and the
    position, and makes each entry of the result's `structuredContent`
    (avtal-mcp's `AmendmentResult`) an `AmendmentInfo`. The tool lists every
    amendment in one result, without pages. An error result is None, and so
    is a result that does not fit (a server of another version): unlike a
    section, whose check cannot do without it, the amendments not known
    give the answer a reservation, and the run goes on.
"""

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from pydantic import BaseModel, ValidationError

from avtalsagent.agent.amendments import AmendmentInfo, AmendmentReader
from avtalsagent.agent.register_reader import RegisterEntry, RegisterReader
from avtalsagent.agent.sections import CitedSection, SectionReader
from avtalsagent.config import Settings
from avtalsagent.domain.identifiers import agreement_key

_log = logging.getLogger(__name__)

# The server's name for the adapter: tool metadata and callbacks carry it.
SERVER_NAME = "avtal"
READ_SECTION = "read_section"
SEARCH_REGISTER = "search_register"
FIND_AMENDMENTS = "find_amendments"
REGISTER_PAGE = 20  # search_register's largest limit


@dataclass(frozen=True)
class McpTools:
    """avtal-mcp's tools for the model, and the readers for the answer check."""

    tools: list[BaseTool]
    reader: SectionReader
    register: RegisterReader
    amendments: AmendmentReader


class McpSectionReader:
    """A `SectionReader` that reads with avtal-mcp's `read_section`, on an open session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def read(self, sha256: str, section_position: int) -> CitedSection | None:
        """The section, or None when the server answers with an error.

        The server's error says why: no such file or section, held back, a
        malformed hash, or the database not answering. Its text is written
        for the model and names no internals, so it is logged as it is.
        """
        result = await self._session.call_tool(
            READ_SECTION, {"sha256": sha256, "section_position": section_position}
        )
        if result.isError:
            message = " ".join(
                block.text for block in result.content if isinstance(block, TextContent)
            )
            _log.info("read_section(%s…, %d): %s", sha256[:12], section_position, message)
            return None
        # A result that does not fit is a server of another version: an error, not None.
        return CitedSection.model_validate(result.structuredContent)


class _RegisterPage(BaseModel):
    """A page of `search_register`'s result (avtal-mcp's `RegisterResult`)."""

    rows: list[RegisterEntry]
    total: int  # every matching row, also those on other pages


class McpRegisterReader:
    """A `RegisterReader` that reads with avtal-mcp's `search_register`, on an open session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def read(self, agreement_number: str) -> list[RegisterEntry] | None:
        """Every row of the agreement, or None when the register has no such agreement.

        An error result (an unknown number, a malformed one, the database
        not answering) is logged as it is, like `McpSectionReader`'s.
        """
        key = agreement_key(agreement_number)
        entries: list[RegisterEntry] = []
        offset = 0
        while True:
            result = await self._session.call_tool(
                SEARCH_REGISTER,
                {"agreement_number": agreement_number, "limit": REGISTER_PAGE, "offset": offset},
            )
            if result.isError:
                message = " ".join(
                    block.text for block in result.content if isinstance(block, TextContent)
                )
                _log.info("search_register(%s): %s", agreement_number, message)
                return None
            # A result that does not fit is a server of another version: an error, not None.
            page = _RegisterPage.model_validate(result.structuredContent)
            rows = [row for row in page.rows if _same_agreement(row, agreement_number, key)]
            if page.rows and not rows:
                # The procurement's agreements: the server has no agreement by this number.
                _log.info(
                    "search_register(%s): only the procurement's agreements", agreement_number
                )
                return None
            entries += rows
            offset += len(page.rows)
            if not page.rows or offset >= page.total:
                return entries or None


class _Amending(BaseModel):
    """Where a change is written: the fields of avtal-mcp's `SectionRef` the rule needs."""

    sha256: str
    section_position: int
    file_title: str
    section_number: str | None
    section_title: str


class _Amended(BaseModel):
    """What a change changes: of avtal-mcp's `TargetRef`, only the section's place."""

    section_position: int | None  # None for the whole file


class _Amendment(BaseModel):
    """An entry of `find_amendments`' result (avtal-mcp's `Amendment`)."""

    amending: _Amending
    amended: _Amended
    status: str
    dated: date | None


class _Amendments(BaseModel):
    """`find_amendments`' result (avtal-mcp's `AmendmentResult`), as far as the rule reads it."""

    amendments: list[_Amendment]


class McpAmendmentReader:
    """An `AmendmentReader` that reads with avtal-mcp's `find_amendments`, on an open session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def read(self, sha256: str, section_position: int) -> list[AmendmentInfo] | None:
        """The amendments of the section and of its file, newest first; None if unreadable.

        An error result (no such section, held back, the database not
        answering) is logged as it is, like `McpSectionReader`'s; a result
        that does not fit is logged without its content.
        """
        result = await self._session.call_tool(
            FIND_AMENDMENTS, {"sha256": sha256, "section_position": section_position}
        )
        if result.isError:
            message = " ".join(
                block.text for block in result.content if isinstance(block, TextContent)
            )
            _log.info("find_amendments(%s…, %d): %s", sha256[:12], section_position, message)
            return None
        try:
            found = _Amendments.model_validate(result.structuredContent)
        except ValidationError:
            # A server of another version: the amendments are not known, so not checked.
            _log.warning(
                "find_amendments(%s…, %d): unreadable result", sha256[:12], section_position
            )
            return None
        return [
            AmendmentInfo(
                **entry.amending.model_dump(),
                amended_position=entry.amended.section_position,
                status=entry.status,
                dated=entry.dated,
            )
            for entry in found.amendments
        ]


def _same_agreement(row: RegisterEntry, number: str, key: str | None) -> bool:
    """True when the row is the agreement `number`, in any spelling (as the server matches)."""
    return row.agreement_number == number or (
        key is not None and agreement_key(row.agreement_number) == key
    )


async def load_tools(session: ClientSession) -> McpTools:
    """The tools and the readers of an open, initialised session."""
    tools = await load_mcp_tools(session, server_name=SERVER_NAME)
    return McpTools(
        tools=tools,
        reader=McpSectionReader(session),
        register=McpRegisterReader(session),
        amendments=McpAmendmentReader(session),
    )


def stdio_parameters() -> StdioServerParameters:
    """How stdio starts avtal-mcp: this interpreter, the server's module, this environment.

    The parameters hold the environment, secrets included: never log them.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "avtalsagent.mcp_server", "stdio"],
        env=dict(os.environ),
    )


@asynccontextmanager
async def open_mcp_session(settings: Settings) -> AsyncIterator[ClientSession]:
    """An initialised session to avtal-mcp over MCP_TRANSPORT, open until the block ends."""
    if settings.mcp_transport == "stdio":
        async with (
            stdio_client(stdio_parameters()) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
    else:
        connection: StreamableHttpConnection = {
            "transport": "streamable_http",
            "url": settings.mcp_url,
        }
        client = MultiServerMCPClient({SERVER_NAME: connection})
        async with client.session(SERVER_NAME) as session:  # initialises it
            yield session


@asynccontextmanager
async def open_mcp_tools(settings: Settings) -> AsyncIterator[McpTools]:
    """avtal-mcp's tools and readers, on one session open until the block ends."""
    async with open_mcp_session(settings) as session:
        yield await load_tools(session)
