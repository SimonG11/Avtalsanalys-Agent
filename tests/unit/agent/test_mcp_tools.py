"""Tests for avtalsagent.agent.mcp_tools: the agent's tools and readers from avtal-mcp.

The real M6 server object is built with stand-ins for the eight tools: each
has the real tool's name, signature and docstring (so the same schemas and
Swedish descriptions), and only `read_section`, `search_register` (over a
few register rows, paged and matched as the real tool does) and
`find_amendments` (for one section) answer. The
client side runs in memory (`create_connected_server_and_client_session`),
over HTTP with uvicorn on this machine, and over stdio with the real server
process; no test opens a database connection or calls OpenAI.
"""

import functools
import inspect
import itertools
import json
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any, cast

import pytest
import uvicorn
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.mcp_tools import (
    REGISTER_PAGE,
    McpAmendmentReader,
    McpRegisterReader,
    McpSectionReader,
    McpTools,
    load_tools,
    open_mcp_tools,
    stdio_parameters,
)
from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.mcp_server.references import SectionReference, TargetRef
from avtalsagent.mcp_server.results import SectionRef
from avtalsagent.mcp_server.server import DATABASE_ERROR, ToolFunction, build_server
from avtalsagent.mcp_server.tools import TOOLS
from avtalsagent.mcp_server.tools.find_amendments import Amendment, AmendmentResult
from avtalsagent.mcp_server.tools.read_section import Section
from avtalsagent.mcp_server.tools.search_register import RegisterResult, RegisterRow

CONTRACT_ORDER = [
    "search_documents",
    "read_section",
    "get_outline",
    "resolve_reference",
    "list_documents",
    "search_register",
    "find_amendments",
    "calculate_date",
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


# --- the amendments find_amendments' stand-in gives for SECTION ---

LOG_SHA = "ef" * 32
AMENDMENT_SHA = "0a" * 32
# The section as find_amendments gives the section and the file asked about.
SECTION_TARGET = TargetRef(
    **SECTION.model_dump(include=set(TargetRef.model_fields)),
)
WHOLE_FILE = TargetRef(
    **{
        **SECTION_TARGET.model_dump(),
        "section_position": None,
        "section_number": None,
        "section_title": None,
        "page_start": None,
        "page_end": None,
    }
)


def amending(
    sha256: str, file_title: str, position: int, number: str | None, title: str
) -> SectionRef:
    return SectionRef(
        sha256=sha256,
        file_title=file_title,
        document_type="questions_and_answers" if sha256 == LOG_SHA else "amendment",
        page_titles=["IT-drift Mindre"],
        section_position=position,
        section_number=number,
        section_title=title,
        page_start=2,
        page_end=2,
    )


# A correction in a questions log, as 7a765d649e25 §9 corrects punkt 3.2 of the tender
# documents; an amendment whose reference has several candidates; one that changes the file.
AMENDMENTS = AmendmentResult(
    target=SECTION_TARGET,
    amendments=[
        Amendment(
            amending=amending(LOG_SHA, "Frågor och svar", 6, "9", "Publik fråga"),
            amended=SECTION_TARGET,
            raw="punkt 6.21.9",
            status="resolved",
            dated=date(2024, 2, 20),
            excerpt="Rättelse. Texten som gäller är följande för punkt 6.21.9: …",
        ),
        Amendment(
            amending=amending(
                AMENDMENT_SHA, "Bilaga 5 Tillägg", 0, None, "Text före första rubriken"
            ),
            amended=SECTION_TARGET,
            raw="Punkt 6.21.9",
            status="ambiguous",
            dated=None,
            excerpt="Punkt 6.21.9 ersätts med följande: …",
        ),
        Amendment(
            amending=amending(AMENDMENT_SHA, "Bilaga 5 Tillägg", 2, "2", "Tillämplig lag"),
            amended=WHOLE_FILE,
            raw="Allmänna villkor",
            status="resolved",
            dated=None,
            excerpt="Allmänna villkor ändras härmed: …",
        ),
    ],
    held_back=1,
)


# --- the register rows search_register's stand-in answers from ---


def register_row(
    agreement_number: str, supplier_name: str, org_number: str, sub_area: str, **fields: Any
) -> RegisterRow:
    procurement_number, _, _ = agreement_number.rpartition("-")
    values: dict[str, Any] = {
        "agreement_number": agreement_number,
        "procurement_number": procurement_number,
        "supplier_name": supplier_name,
        "org_number": org_number,
        "former_names": [],
        "framework_area": sub_area.split(" / ")[0],
        "sub_area": sub_area,
        "valid_from": date(2024, 11, 23),
        "valid_to": date(2028, 11, 22),
        "max_extension_to": None,
        **fields,
    }
    return RegisterRow(**values)


# Novare Public HR AB has 34 rows in the register; 24 here, more than one page of 20.
NOVARE = "23.3-11976-2023-007"
NOVARE_ROWS = [
    register_row(
        NOVARE, "Novare Public HR AB", "559125-5350", f"Rekryteringstjänster / {kind} / {region}"
    )
    for kind, region in itertools.product(
        [
            "Rekrytering av IT-chefer och IT-specialister",
            "Rekrytering av chefer",
            "Rekrytering av kontorspersonal",
            "Rekrytering av specialister",
        ],
        [
            "Mellersta Norrland",
            "Norra Mellansverige",
            "Stockholm",
            "Västsverige",
            "Östra Mellansverige",
            "Övre Norrland",
        ],
    )
]
# Another agreement of Novare's procurement, which the procurement's number also gives.
EXPERIS_ROWS = [
    register_row(
        "23.3-11976-2023-005",
        "Experis AB",
        "556855-1104",
        f"Rekryteringstjänster / Rekrytering av chefer / {region}",
    )
    for region in ["Stockholm", "Västsverige"]
]
NORDLO_ADVANCE = register_row(
    "23.3-5890-2023-002",
    "Nordlo Advance AB",
    "556486-1689",
    "IT-drift / IT-drift Mindre, upp till 200 anställda",
    former_names=["EPM Data"],
    valid_from=date(2024, 11, 14),
    valid_to=date(2028, 11, 13),
)
# The register writes one agreement two ways, on different rows.
DIGITAL_INTERPRETATIONS = [
    register_row(
        number,
        "Digital Interpretations Scandinavia AB",
        "559032-5394",
        f"Tolkförmedlingstjänster / {county}",
    )
    for number, county in [
        ("23.3-12000-2020-001", "Stockholms län"),
        ("23.3-12000-2020-01", "Örebro län"),
    ]
]
REGISTER = [*EXPERIS_ROWS, *NOVARE_ROWS, NORDLO_ADVANCE, *DIGITAL_INTERPRETATIONS]
NO_SUCH_NUMBER = "Numret {number} finns inte i registret, varken som avtal eller som upphandling."
# The arguments of each search_register call, in order.
register_calls: list[dict[str, Any]] = []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_register_calls_yet() -> None:
    register_calls.clear()


# --- the M6 server with the real tools' schemas and a read_section that answers ---


def answer_read_section(
    sha256: str, section_number: str | None = None, section_position: int | None = None
) -> Section:
    if (sha256, section_position) == (SHA, SECTION.section_position):
        return SECTION
    raise NotFoundError(NOT_FOUND.format(position=section_position, sha=sha256[:12]))


def answer_search_register(
    agreement_number: str | None = None, limit: int = 20, offset: int = 0, **other: Any
) -> RegisterResult:
    """As the real tool: every spelling of the agreement, else the procurement's agreements."""
    register_calls.append({"agreement_number": agreement_number, "limit": limit, "offset": offset})
    assert agreement_number is not None and not any(other.values())
    key = agreement_key(agreement_number)
    rows = [
        row
        for row in REGISTER
        if row.agreement_number == agreement_number
        or (key is not None and agreement_key(row.agreement_number) == key)
    ]
    procurement = procurement_key(agreement_number)
    if not rows and procurement is not None:
        rows = [row for row in REGISTER if procurement_key(row.procurement_number) == procurement]
    if not rows:
        raise NotFoundError(NO_SUCH_NUMBER.format(number=agreement_number))
    rows.sort(key=lambda row: (row.framework_area, row.agreement_number, row.sub_area))
    return RegisterResult(rows=rows[offset : offset + limit], total=len(rows))


def answer_find_amendments(
    sha256: str, section_number: str | None = None, section_position: int | None = None
) -> AmendmentResult:
    if (sha256, section_position) == (SHA, SECTION.section_position):
        return AMENDMENTS
    if (sha256, section_position) == (SHA, 37):  # 6.21.4 Hävning: no amendments
        return AmendmentResult(target=SECTION_TARGET, amendments=[], held_back=0)
    raise NotFoundError(NOT_FOUND.format(position=section_position, sha=sha256[:12]))


def stand_in(real: ToolFunction, answer: Callable[..., BaseModel] | None) -> ToolFunction:
    """The real tool's name, docstring and signature (`__wrapped__`), with `answer` as its body."""

    @functools.wraps(real)
    def tool(*session_and_embedder: Any, **arguments: Any) -> BaseModel:
        if answer is None:
            raise AssertionError(f"the test called {real.__name__}")
        return answer(**arguments)

    return tool


def stand_in_server() -> FastMCP:
    answers: dict[str, Callable[..., BaseModel]] = {
        "read_section": answer_read_section,
        "search_register": answer_search_register,
        "find_amendments": answer_find_amendments,
    }
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
async def test_the_tools_are_avtal_mcps_eight_in_the_contracts_order(mcp_tools: McpTools) -> None:
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


# --- the register reader ---


def entries(rows: list[RegisterRow]) -> list[RegisterEntry]:
    return [RegisterEntry.model_validate(row.model_dump()) for row in rows]


@pytest.mark.anyio
async def test_the_register_reader_gives_each_row_as_a_register_entry(
    mcp_tools: McpTools,
) -> None:
    assert await mcp_tools.register.read("23.3-5890-2023-002") == [
        RegisterEntry(
            agreement_number="23.3-5890-2023-002",
            procurement_number="23.3-5890-2023",
            supplier_name="Nordlo Advance AB",
            org_number="556486-1689",
            former_names=["EPM Data"],
            framework_area="IT-drift",
            sub_area="IT-drift / IT-drift Mindre, upp till 200 anställda",
            valid_from=date(2024, 11, 14),
            valid_to=date(2028, 11, 13),
            max_extension_to=None,
        )
    ]
    assert register_calls == [
        {"agreement_number": "23.3-5890-2023-002", "limit": REGISTER_PAGE, "offset": 0}
    ]


@pytest.mark.anyio
async def test_the_register_reader_reads_every_page_of_an_agreement(
    mcp_tools: McpTools,
) -> None:
    read = await mcp_tools.register.read(NOVARE)

    assert read == entries(sorted(NOVARE_ROWS, key=lambda row: row.sub_area))
    assert REGISTER_PAGE == 20  # search_register's largest limit
    assert register_calls == [
        {"agreement_number": NOVARE, "limit": 20, "offset": 0},
        {"agreement_number": NOVARE, "limit": 20, "offset": 20},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "number", ["23.3-12000-2020-001", "23.3-12000-2020-01", "23.3.12000-20:001"]
)
async def test_the_register_reader_gives_the_rows_of_every_spelling_of_the_agreement(
    mcp_tools: McpTools, number: str
) -> None:
    assert await mcp_tools.register.read(number) == entries(DIGITAL_INTERPRETATIONS)


@pytest.mark.anyio
async def test_the_register_reader_gives_none_for_a_procurement_and_stops_at_its_first_page(
    mcp_tools: McpTools,
) -> None:
    # The server answers a procurement's number with all its agreements: not one agreement.
    assert await mcp_tools.register.read("23.3-11976-2023") is None
    assert [call["offset"] for call in register_calls] == [0]


@pytest.mark.anyio
async def test_the_register_reader_gives_none_for_an_unknown_number_and_logs_why(
    mcp_tools: McpTools, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="avtalsagent.agent.mcp_tools"):
        read = await mcp_tools.register.read("23.3-11976-2023-099")

    assert read is None
    assert NO_SUCH_NUMBER.format(number="23.3-11976-2023-099") in caplog.text


@pytest.mark.anyio
async def test_the_register_reader_gives_none_for_a_number_the_tool_refuses(
    mcp_tools: McpTools,
) -> None:
    assert await mcp_tools.register.read("23.3") is None  # shorter than the tool takes
    assert register_calls == []  # the server refused it before the tool ran


@pytest.mark.anyio
async def test_the_register_reader_is_an_mcp_register_reader(mcp_tools: McpTools) -> None:
    assert isinstance(mcp_tools.register, McpRegisterReader)


# --- the amendment reader ---


@pytest.mark.anyio
async def test_the_amendment_reader_gives_each_amendment_with_what_it_changes(
    mcp_tools: McpTools,
) -> None:
    assert await mcp_tools.amendments.read(SHA, SECTION.section_position) == [
        AmendmentInfo(
            sha256=LOG_SHA,
            section_position=6,
            file_title="Frågor och svar",
            section_number="9",
            section_title="Publik fråga",
            amended_position=42,
            status="resolved",
            dated=date(2024, 2, 20),
        ),
        AmendmentInfo(
            sha256=AMENDMENT_SHA,
            section_position=0,
            file_title="Bilaga 5 Tillägg",
            section_number=None,
            section_title="Text före första rubriken",
            amended_position=42,
            status="ambiguous",
            dated=None,
        ),
        AmendmentInfo(
            sha256=AMENDMENT_SHA,
            section_position=2,
            file_title="Bilaga 5 Tillägg",
            section_number="2",
            section_title="Tillämplig lag",
            amended_position=None,  # the whole file
            status="resolved",
            dated=None,
        ),
    ]


@pytest.mark.anyio
async def test_the_amendment_reader_gives_an_empty_list_for_a_section_never_changed(
    mcp_tools: McpTools,
) -> None:
    assert await mcp_tools.amendments.read(SHA, 37) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sha256", "position"),
    [(SHA, 7), (OTHER_SHA, 42), ("not-a-hash", 42), (SHA, -1)],
    ids=["no such section", "no such file", "a malformed hash", "a negative position"],
)
async def test_the_amendment_reader_gives_none_for_a_section_the_server_does_not_give(
    mcp_tools: McpTools, sha256: str, position: int
) -> None:
    assert await mcp_tools.amendments.read(sha256, position) is None


@pytest.mark.anyio
async def test_the_amendment_reader_logs_why_it_gives_none(
    mcp_tools: McpTools, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="avtalsagent.agent.mcp_tools"):
        assert await mcp_tools.amendments.read(SHA, 7) is None

    assert NOT_FOUND.format(position=7, sha=SHA[:12]) in caplog.text


class OneResult:
    """A session that answers every tool call with one result."""

    def __init__(self, result: CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, arguments))
        return self.result


@pytest.mark.anyio
async def test_the_amendment_reader_gives_none_for_a_result_of_another_version() -> None:
    # A server whose result has other fields: the amendments are not known, so not checked.
    session = OneResult(
        CallToolResult(content=[], structuredContent={"amendments": [{"source": "9"}]})
    )
    reader = McpAmendmentReader(cast(ClientSession, session))

    assert await reader.read(SHA, 42) is None
    assert session.calls == [("find_amendments", {"sha256": SHA, "section_position": 42})]


@pytest.mark.anyio
async def test_the_amendment_reader_is_an_mcp_amendment_reader(mcp_tools: McpTools) -> None:
    assert isinstance(mcp_tools.amendments, McpAmendmentReader)


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
