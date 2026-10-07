"""Integration test: list_documents and search_register against a real Postgres.

What:
    Runs `list_documents` and `search_register` on the corpus of
    `test_index_store.py` (two agreement pages, six files of real clauses,
    the procurement document's 6.21.2.4 held back, three register rows),
    indexed with the stand-in embedder, plus one invented register row:
    NetBin's agreement in a second sub-area, under another name with a
    former name, with other dates and a maximum extension. Checks every
    filter alone and combined, the register's spelling of areas and
    numbers, an agreement the register writes two ways, a procurement
    number for its agreements, unknown values and missing filters as errors,
    the limit, the offset and the total, that % and _ in a supplier name are
    characters, that a file held back whole is never listed, that
    `list_documents` says the index is missing between `process` and
    `index`, and one call of each tool through the MCP SDK's in-memory
    client.

Why:
    Which files and rows a filter keeps is SQL (the scope's arrays, the
    agreement's procurement, ILIKE with escaping, date ranges) that unit
    tests cannot cover. A list that lets a held-back file through would show
    the agent a document step 5 refused.

How:
    Uses the `engine` fixture from conftest.py and the corpus, `build_index`
    and `TopicEmbedder` of `test_index_store.py`. The register is loaded
    again with the extra row before the index is built; the row has the same
    procurement and area as NetBin's, so every file's scope is as in the
    other tests. One test loads the register again with a second spelling
    of Advania's number and builds the index again. The tools read through a
    read-only engine, as the server does; changes to the data go through the
    writable `engine` first.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.domain.extracted import DocumentType, Finding, Severity
from avtalsagent.ingestion.extraction_store import save_findings
from avtalsagent.ingestion.index_store import clear_index
from avtalsagent.mcp_server.errors import MissingArgumentError, NotFoundError, UnavailableError
from avtalsagent.mcp_server.server import build_server
from avtalsagent.mcp_server.tools.list_documents import DocumentEntry, DocumentList, list_documents
from avtalsagent.mcp_server.tools.search_register import (
    RegisterResult,
    RegisterRow,
    search_register,
)
from avtalsagent.register.load import load_register
from tests.integration.test_index_store import (
    ADVANIA,
    ADVANIA_CARD,
    BEMANNING,
    HELD,
    IT_DRIFT,
    MAIN,
    NETBIN,
    NETBIN_CARD,
    PROCUREMENT,
    REGISTER,
    REGISTER_VERSION,
    TEMPLATE,
    TERMS,
    TopicEmbedder,
    build_index,
    register_row,
    store_corpus,
)

IT_DRIFT_TITLE = "IT-drift Mindre, upp till 200 anställda"
BEMANNING_TITLE = "Bemanningstjänster - IT-tjänster upp till 1000 timmar"
A_HUB = "23.3-14537-2023-001"  # Bemanningstjänster; no card of its own in the corpus
UNKNOWN = "23.3-9999-2023-001"
ADVANIA_ORG = "556214-9996"
NETBIN_ORG = "556710-0267"

# The sub-area paths as `sub_area.path` stores them.
MINDRE = "IT-drift / IT-drift Mindre, upp till 200 anställda"
EXTRA = "IT-drift / Tilläggstjänster"
HUB = "Bemanningstjänster / Bemanningstjänster - IT-tjänster upp till 1000 timmar"

# Invented: NetBin's agreement in a second sub-area, under another of its names (with a
# former one), with its own dates and a maximum extension. The agreement keeps the supplier
# name of its first row.
NETBIN_SECOND = register_row(
    2427, NETBIN, "NetBin AB", NETBIN_ORG, "IT-drift", "Tilläggstjänster"
).model_copy(
    update={
        "former_supplier_name": "Exempelnät AB",
        "valid_from": date(2025, 1, 1),
        "valid_to": date(2026, 12, 31),
        "max_extension_to": date(2027, 12, 31),
    }
)

# Invented: Advania's agreement written a second way on a row of its own, as the 2026-10-05
# list writes 23.3-12000-2020-001 on 14 rows and 23.3-12000-2020-01 on one. Both have one key.
ADVANIA_SHORT = "23.3-5890-2023-03"
ADVANIA_SECOND = register_row(
    150, ADVANIA_SHORT, "Advania Sverige AB", ADVANIA_ORG, "IT-drift", "Tilläggstjänster"
)

TERMS_ENTRY = DocumentEntry(
    sha256=TERMS,
    file_title="Allmänna villkor",
    document_type="general_terms",
    page_titles=[IT_DRIFT_TITLE],
    framework_areas=["IT-drift"],
    agreement_numbers=[],
    version_date=None,
    section_count=2,
)
ADVANIA_ENTRY = DocumentEntry(
    sha256=ADVANIA_CARD,
    file_title="Ramavtal",
    document_type="supplier_agreement",
    page_titles=[IT_DRIFT_TITLE],
    framework_areas=["IT-drift"],
    agreement_numbers=[ADVANIA],
    version_date=None,
    section_count=2,
)
NETBIN_ENTRY = ADVANIA_ENTRY.model_copy(
    update={"sha256": NETBIN_CARD, "agreement_numbers": [NETBIN]}
)
PROCUREMENT_ENTRY = DocumentEntry(
    sha256=PROCUREMENT,
    file_title="Upphandlingsdokument",
    document_type="procurement_document",
    page_titles=[IT_DRIFT_TITLE],
    framework_areas=["IT-drift"],
    agreement_numbers=[],
    version_date=None,
    section_count=1,  # its 6.21.2.4 is held back
)
TEMPLATE_ENTRY = DocumentEntry(
    sha256=TEMPLATE,
    file_title="Utkast till Säkerhetsskyddsavtal",
    document_type="template",
    page_titles=[BEMANNING_TITLE, IT_DRIFT_TITLE],
    framework_areas=["Bemanningstjänster", "IT-drift"],
    agreement_numbers=[],
    version_date=None,
    section_count=2,
)

ADVANIA_ROW = RegisterRow(
    agreement_number=ADVANIA,
    procurement_number=IT_DRIFT,
    supplier_name="Advania Sverige AB",
    org_number=ADVANIA_ORG,
    former_names=[],
    framework_area="IT-drift",
    sub_area=MINDRE,
    valid_from=date(2024, 11, 14),
    valid_to=date(2028, 11, 13),
    max_extension_to=None,
)

IT_DRIFT_ROWS = [(NETBIN, MINDRE), (NETBIN, EXTRA), (ADVANIA, MINDRE)]
NETBIN_ROWS = [(NETBIN, MINDRE), (NETBIN, EXTRA)]


@pytest.fixture
def indexed(engine: Engine) -> Engine:
    """The corpus with the extra register row, as `process` stores it, and its index."""
    store_corpus(engine)
    with session_factory(engine).begin() as session:
        load_register(session, REGISTER_VERSION, [*REGISTER, NETBIN_SECOND])
    build_index(engine, TopicEmbedder())
    return engine


@pytest.fixture
def read_only(indexed: Engine) -> Iterator[Engine]:
    """An engine on the same database that refuses writes, as the server's does."""
    engine = create_db_engine(indexed.url.render_as_string(hide_password=False), read_only=True)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(read_only: Engine) -> sessionmaker[Session]:
    return session_factory(read_only)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def listed(result: DocumentList) -> list[str]:
    return [document.sha256 for document in result.documents]


def found(result: RegisterResult) -> list[tuple[str, str]]:
    return [(row.agreement_number, row.sub_area) for row in result.rows]


# --- list_documents -----------------------------------------------------------------------------


def test_an_area_lists_its_documents_agreement_first(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    with session_factory(indexed).begin() as session:
        session.execute(
            update(models.DocumentMetadata)
            .where(models.DocumentMetadata.sha256 == TERMS)
            .values(version_date=date(2023, 9, 1))
        )

    with sessions() as session:
        result = list_documents(session, framework_area="IT-drift")

    # The agreement's documents by title (the two cards, both "Ramavtal", by sha256), then
    # the procurement's, then the support for call-offs.
    assert result == DocumentList(
        documents=[
            TERMS_ENTRY.model_copy(update={"version_date": date(2023, 9, 1)}),
            ADVANIA_ENTRY,
            NETBIN_ENTRY,
            PROCUREMENT_ENTRY,
            TEMPLATE_ENTRY,
        ],
        total=5,
    )


@pytest.mark.parametrize(
    ("filters", "files"),
    [
        # In another case than the register's: the tool uses the register's spelling.
        ({"framework_area": "bemanningstjänster"}, [MAIN, TEMPLATE]),
        # The card and the procurement's shared files; not NetBin's card.
        ({"agreement_number": ADVANIA}, [TERMS, ADVANIA_CARD, PROCUREMENT, TEMPLATE]),
        ({"agreement_number": "23.3.5890-23-003"}, [TERMS, ADVANIA_CARD, PROCUREMENT, TEMPLATE]),
        ({"agreement_number": NETBIN}, [TERMS, NETBIN_CARD, PROCUREMENT, TEMPLATE]),
        ({"agreement_number": A_HUB}, [MAIN, TEMPLATE]),
        # A procurement number (no supplier's sequence): every file of the procurement.
        (
            {"agreement_number": IT_DRIFT},
            [TERMS, ADVANIA_CARD, NETBIN_CARD, PROCUREMENT, TEMPLATE],
        ),
        (
            {"agreement_number": "23.3.5890-23"},
            [TERMS, ADVANIA_CARD, NETBIN_CARD, PROCUREMENT, TEMPLATE],
        ),
        ({"agreement_number": BEMANNING}, [MAIN, TEMPLATE]),
        # The template is in both areas and shared in both procurements.
        ({"framework_area": "Bemanningstjänster", "agreement_number": ADVANIA}, [TEMPLATE]),
        ({"document_type": DocumentType.GENERAL_TERMS}, [TERMS]),
        ({"document_type": DocumentType.SUPPLIER_AGREEMENT}, [ADVANIA_CARD, NETBIN_CARD]),
        (
            {"agreement_number": ADVANIA, "document_type": DocumentType.SUPPLIER_AGREEMENT},
            [ADVANIA_CARD],
        ),
        ({"document_type": DocumentType.AMENDMENT}, []),
    ],
    ids=[
        "area",
        "agreement",
        "agreement spelt otherwise",
        "other agreement",
        "agreement without a card",
        "procurement",
        "procurement spelt otherwise",
        "other procurement",
        "area and agreement",
        "general terms",
        "cards",
        "agreement and type",
        "no such type",
    ],
)
def test_the_filters_keep_the_files_of_their_scope(
    sessions: sessionmaker[Session], filters: dict[str, Any], files: list[str]
) -> None:
    with sessions() as session:
        result = list_documents(session, **filters)

    assert listed(result) == files
    assert result.total == len(files)


def test_the_limit_cuts_the_list_but_not_the_total(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        result = list_documents(session, framework_area="IT-drift", limit=2)

    assert result == DocumentList(documents=[TERMS_ENTRY, ADVANIA_ENTRY], total=5)


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (
            {"agreement_number": UNKNOWN},
            "Numret 23.3-9999-2023-001 finns inte i registret, varken som avtal eller som "
            "upphandling. Sök avtalet med search_register",
        ),
        ({"agreement_number": "23.3-9999-2023"}, "Numret 23.3-9999-2023 finns inte i registret"),
        # A sequence the procurement does not have is not widened to the procurement.
        ({"agreement_number": "23.3-5890-2023-009"}, "Numret 23.3-5890-2023-009 finns inte"),
        (
            {"framework_area": "Möbler"},
            "'Möbler' finns inte i registret. Områden: Bemanningstjänster, IT-drift.",
        ),
    ],
    ids=["agreement", "procurement", "sequence", "area"],
)
def test_a_document_filter_the_register_does_not_know_is_an_error(
    sessions: sessionmaker[Session], filters: dict[str, Any], message: str
) -> None:
    with sessions() as session, pytest.raises(NotFoundError, match=message):
        list_documents(session, **filters)


def test_a_file_held_back_whole_is_never_listed(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # After the index was built, so the files keep their scopes; the tool checks the findings.
    findings = [
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject="6.21.2.4",
            message="Avsnitt 6.21.2.4 ligger på en skannad sida utan text.",
            sha256=PROCUREMENT,
            section=HELD.section_position,
        ),
        Finding(
            check="still_published",
            severity=Severity.QUARANTINE,
            subject=NETBIN_CARD,
            message="Ingen avtalssida på avropa.se länkar längre till filen.",
            sha256=NETBIN_CARD,
        ),
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject="6.21.4",
            message="Avsnitt 6.21.4 ligger på en skannad sida utan text.",
            sha256=TERMS,
            section=1,
        ),
    ]
    with session_factory(indexed).begin() as session:
        save_findings(session, findings)

    with sessions() as session:
        area = list_documents(session, framework_area="IT-drift")
        netbin = list_documents(session, agreement_number=NETBIN)
        cards = list_documents(session, document_type=DocumentType.SUPPLIER_AGREEMENT)

    assert area == DocumentList(
        documents=[
            TERMS_ENTRY.model_copy(update={"section_count": 1}),  # its 6.21.4 is held back
            ADVANIA_ENTRY,
            PROCUREMENT_ENTRY,
            TEMPLATE_ENTRY,
        ],
        total=4,
    )
    assert listed(netbin) == [TERMS, PROCUREMENT, TEMPLATE]
    assert netbin.total == 3
    assert cards == DocumentList(documents=[ADVANIA_ENTRY], total=1)


def test_between_process_and_index_list_documents_says_the_index_is_missing(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # As after `process`: the sections were stored again and the index emptied. An empty list
    # would read as "the agreement has no documents".
    with session_factory(indexed).begin() as session:
        clear_index(session)

    with sessions() as session:
        with pytest.raises(UnavailableError, match="Sökindexet är inte byggt"):
            list_documents(session, framework_area="IT-drift")
        with pytest.raises(UnavailableError, match="Sökindexet är inte byggt"):
            list_documents(session, agreement_number=ADVANIA)
        register = search_register(session, framework_area="IT-drift")

    assert register.total == 3  # the register is not part of the index


# --- search_register ----------------------------------------------------------------------------


def test_a_row_is_an_agreement_in_a_sub_area_with_the_registers_dates(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        advania = search_register(session, supplier="advania")
        netbin = search_register(session, org_number=NETBIN_ORG)

    assert advania == RegisterResult(rows=[ADVANIA_ROW], total=1)
    # One supplier name for the agreement (its first row's), the former names of every name
    # its org number has; dates per sub-area.
    assert netbin == RegisterResult(
        rows=[
            RegisterRow(
                agreement_number=NETBIN,
                procurement_number=IT_DRIFT,
                supplier_name="NetBin Sverige AB",
                org_number=NETBIN_ORG,
                former_names=["Exempelnät AB"],  # under its other name, on both rows
                framework_area="IT-drift",
                sub_area=MINDRE,
                valid_from=date(2024, 11, 14),
                valid_to=date(2028, 11, 13),
                max_extension_to=None,
            ),
            RegisterRow(
                agreement_number=NETBIN,
                procurement_number=IT_DRIFT,
                supplier_name="NetBin Sverige AB",
                org_number=NETBIN_ORG,
                former_names=["Exempelnät AB"],  # under its other name, on both rows
                framework_area="IT-drift",
                sub_area=EXTRA,
                valid_from=date(2025, 1, 1),
                valid_to=date(2026, 12, 31),
                max_extension_to=date(2027, 12, 31),
            ),
        ],
        total=2,
    )


@pytest.mark.parametrize(
    ("filters", "rows"),
    [
        ({"supplier": "ADVANIA"}, [(ADVANIA, MINDRE)]),
        ({"supplier": "sverige"}, IT_DRIFT_ROWS),
        ({"supplier": "NetBin AB"}, NETBIN_ROWS),  # another name of its org number
        ({"supplier": "exempelnät"}, NETBIN_ROWS),  # a former name
        ({"supplier": " a  hub "}, [(A_HUB, HUB)]),  # spaces as the register writes names
        ({"supplier": "ab"}, [(A_HUB, HUB), *IT_DRIFT_ROWS]),
        ({"agreement_number": ADVANIA}, [(ADVANIA, MINDRE)]),
        ({"agreement_number": "23.3.5890-23-003"}, [(ADVANIA, MINDRE)]),
        # A procurement number: every agreement of the procurement, in any spelling.
        ({"agreement_number": IT_DRIFT}, IT_DRIFT_ROWS),
        ({"agreement_number": "23.3.5890-23"}, IT_DRIFT_ROWS),
        ({"agreement_number": BEMANNING}, [(A_HUB, HUB)]),
        ({"framework_area": "it-drift"}, IT_DRIFT_ROWS),
        ({"framework_area": "Bemanningstjänster"}, [(A_HUB, HUB)]),
        ({"org_number": "5562149996"}, [(ADVANIA, MINDRE)]),
        ({"org_number": "SE556710026701"}, NETBIN_ROWS),  # the VAT number
        ({"org_number": "556000-0000"}, []),
        ({"supplier": "sverige", "framework_area": "Bemanningstjänster"}, []),
        ({"supplier": "netbin", "agreement_number": ADVANIA}, []),
        ({"org_number": NETBIN_ORG, "agreement_number": IT_DRIFT}, NETBIN_ROWS),
        # The second sub-area ended 2026-12-31; its maximum extension does not count.
        (
            {"agreement_number": IT_DRIFT, "valid_on": date(2027, 6, 1)},
            [(NETBIN, MINDRE), (ADVANIA, MINDRE)],
        ),
        # The first and the last day are inside.
        ({"supplier": "ab", "valid_on": date(2024, 11, 14)}, [(NETBIN, MINDRE), (ADVANIA, MINDRE)]),
        ({"supplier": "ab", "valid_on": date(2029, 4, 2)}, [(A_HUB, HUB)]),
        ({"supplier": "ab", "valid_on": date(2029, 4, 3)}, []),
    ],
)
def test_the_register_filters_alone_and_together(
    sessions: sessionmaker[Session], filters: dict[str, Any], rows: list[tuple[str, str]]
) -> None:
    with sessions() as session:
        result = search_register(session, **filters)

    assert found(result) == rows
    assert result.total == len(rows)


def test_an_agreement_the_register_writes_two_ways_is_found_by_either(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    with session_factory(indexed).begin() as session:
        load_register(session, REGISTER_VERSION, [*REGISTER, NETBIN_SECOND, ADVANIA_SECOND])
    build_index(indexed, TopicEmbedder())

    numbers = [ADVANIA, ADVANIA_SHORT, "23.3.5890-23-003"]
    with sessions() as session:
        stored = session.scalar(
            select(models.DocumentScope.agreement_numbers).where(
                models.DocumentScope.sha256 == ADVANIA_CARD
            )
        )
        documents = [listed(list_documents(session, agreement_number=n)) for n in numbers]
        rows = [found(search_register(session, agreement_number=n)) for n in numbers]

    # Step 6 stores one spelling per key in the scopes, so one of the numbers is not it.
    assert stored in ([ADVANIA], [ADVANIA_SHORT])
    assert documents == [[TERMS, ADVANIA_CARD, PROCUREMENT, TEMPLATE]] * 3  # the card too
    assert rows == [[(ADVANIA, MINDRE), (ADVANIA_SHORT, EXTRA)]] * 3  # the rows of both


@pytest.mark.parametrize("supplier", ["Advania%", "a_hub", "%%"])
def test_percent_and_underscore_in_a_name_are_characters(
    sessions: sessionmaker[Session], supplier: str
) -> None:
    # As LIKE wildcards they would find Advania, A Hub Group and every supplier.
    with sessions() as session:
        result = search_register(session, supplier=supplier)

    assert result == RegisterResult(rows=[], total=0)


def test_the_limit_cuts_the_rows_but_not_the_total(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        result = search_register(session, supplier="ab", limit=2)

    assert found(result) == [(A_HUB, HUB), (NETBIN, MINDRE)]
    assert result.total == 4


@pytest.mark.parametrize(
    ("offset", "rows"),
    [(0, [(A_HUB, HUB), (NETBIN, MINDRE)]), (2, [(NETBIN, EXTRA), (ADVANIA, MINDRE)]), (4, [])],
)
def test_the_offset_pages_through_the_rows_in_order(
    sessions: sessionmaker[Session], offset: int, rows: list[tuple[str, str]]
) -> None:
    with sessions() as session:
        result = search_register(session, supplier="ab", limit=2, offset=offset)

    assert found(result) == rows
    assert result.total == 4  # every row, the skipped ones too


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (
            {"agreement_number": UNKNOWN},
            "Numret 23.3-9999-2023-001 finns inte i registret, varken som avtal eller som "
            "upphandling. Sök på leverantörens namn med supplier",
        ),
        ({"agreement_number": "23.3-9999-2023"}, "Numret 23.3-9999-2023 finns inte i registret"),
        # A sequence the procurement does not have is not widened to the procurement.
        ({"agreement_number": "23.3-5890-2023-009"}, "Numret 23.3-5890-2023-009 finns inte"),
        (
            {"supplier": "advania", "framework_area": "Möbler"},
            "'Möbler' finns inte i registret. Områden: Bemanningstjänster, IT-drift.",
        ),
    ],
    ids=["agreement", "procurement", "sequence", "area"],
)
def test_a_register_filter_the_register_does_not_know_is_an_error(
    sessions: sessionmaker[Session], filters: dict[str, Any], message: str
) -> None:
    with sessions() as session, pytest.raises(NotFoundError, match=message):
        search_register(session, **filters)


def test_without_a_filter_each_tool_says_what_it_needs(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        with pytest.raises(
            MissingArgumentError, match="framework_area, agreement_number och document_type"
        ):
            list_documents(session)
        with pytest.raises(
            MissingArgumentError, match="supplier, agreement_number, framework_area och org_number"
        ):
            search_register(session, valid_on=date(2026, 10, 6))


# --- through MCP --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_each_tool_answers_through_mcp_with_structured_content(
    sessions: sessionmaker[Session],
) -> None:
    server = build_server(sessions, TopicEmbedder(), allowed_hosts=["localhost:*"])
    async with create_connected_server_and_client_session(server) as client:
        documents = await client.call_tool(
            "list_documents", {"agreement_number": ADVANIA, "limit": 2}
        )
        register = await client.call_tool(
            "search_register", {"supplier": "advania", "valid_on": "2026-10-06"}
        )
        unknown = await client.call_tool("search_register", {"framework_area": "Möbler"})

    assert documents.isError is False
    assert documents.structuredContent == {
        "documents": [TERMS_ENTRY.model_dump(mode="json"), ADVANIA_ENTRY.model_dump(mode="json")],
        "total": 4,
    }
    assert register.isError is False
    assert register.structuredContent == {
        "rows": [ADVANIA_ROW.model_dump(mode="json")],
        "total": 1,
    }
    assert unknown.isError is True
    assert unknown.structuredContent is None
    assert isinstance(unknown.content[0], TextContent)
    assert "'Möbler' finns inte i registret" in unknown.content[0].text
