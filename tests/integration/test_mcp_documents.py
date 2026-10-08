"""Integration test: the document tools of avtal-mcp against a real Postgres.

What:
    Runs `search_documents`, `read_section`, `get_outline` and
    `resolve_reference` on the corpus of `test_index_store.py` (two agreement
    pages, six files of real clauses, the procurement document's 6.21.2.4
    held back), indexed with the stand-in embedder and given a few
    references as step 4 stores them. Checks what each tool returns, that
    the filters go through the register, that held-back sections and files
    are left out and counted (once, also when found through two pages) or
    refused with a message that says why, that
    the tools show nothing between `process` and `index`, that the server's
    engine refuses writes, and one call of each tool through the MCP SDK's
    in-memory client. With the register loaded again with an area the
    corpus has no files for, as the real register has all 51 areas and
    documents are loaded for four, it checks that `search_documents` and
    `list_documents` refuse that area, its agreement and its procurement
    with a message naming the loaded areas, also next to a loaded area,
    with a document type, and when every file of an area is held back,
    but not without an index, and that the corpus's own areas and
    agreements answer as before.

Why:
    The tools are where the quarantine meets the agent: a section step 5
    holds back must not reach it by its hash, by its number, through an
    outline or by following a reference. Which sections a number, a filter
    or a reference finds is SQL that unit tests cannot cover, and so is
    which filters some shown file matches.

How:
    Uses the `engine` fixture from conftest.py and the corpus, `build_index`
    and `TopicEmbedder` of `test_index_store.py`. The tools read through a
    read-only engine, as the server does (`create_db_engine(read_only=True)`),
    and one test shows that this engine refuses a write. Changes to the data
    (an emptied index, a new finding, the register loaded again) go through
    the writable `engine` first. The invented register row has a procurement
    of its own, so no file's scope changes and the index is not built again.
"""

import re
from collections.abc import Iterator
from typing import Any

import psycopg.errors
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.config import get_settings
from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentType,
    Finding,
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
    Severity,
)
from avtalsagent.ingestion.extraction_store import save_extraction, save_findings
from avtalsagent.ingestion.index_store import clear_index
from avtalsagent.mcp_server.errors import AmbiguousError, NotFoundError, UnavailableError
from avtalsagent.mcp_server.references import SectionReference, TargetRef
from avtalsagent.mcp_server.results import SectionRef
from avtalsagent.mcp_server.server import build_server
from avtalsagent.mcp_server.tools.get_outline import Outline, OutlineSection, get_outline
from avtalsagent.mcp_server.tools.list_documents import list_documents
from avtalsagent.mcp_server.tools.read_section import Section, read_section
from avtalsagent.mcp_server.tools.resolve_reference import resolve_reference
from avtalsagent.mcp_server.tools.search_documents import SectionCopy, search_documents
from avtalsagent.register.load import load_register
from tests.integration.test_index_store import (
    ADVANIA_CARD,
    CORPUS,
    HELD,
    MAIN,
    NETBIN_CARD,
    PENALTY,
    PRICE_ADJUSTMENT,
    PROCUREMENT,
    REGISTER,
    REGISTER_VERSION,
    SECURITY_END,
    TEMPLATE,
    TERMS,
    Clause,
    TopicEmbedder,
    build_index,
    metadata,
    register_row,
    store_corpus,
)

IT_DRIFT_TITLE = "IT-drift Mindre, upp till 200 anställda"
BEMANNING_TITLE = "Bemanningstjänster - IT-tjänster upp till 1000 timmar"
UNKNOWN_FILE = "0" * 64
OTHER_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift/it-drift-storre/"
A_HUB = "23.3-14537-2023-001"  # Bemanningstjänster; no card of its own in the corpus

# Invented: an agreement in an area the corpus has no files for, with a procurement of its own.
FURNITURE_AREA = "Möbler och inredning"
FURNITURE_PROCUREMENT = "23.3-10777-2024"
FURNITURE = "23.3-10777-2024-002"
FURNITURE_ROW = register_row(
    310, FURNITURE, "Exempelmöbler AB", "556677-8899", FURNITURE_AREA, "Kontorsmöbler"
)
NOT_LOADED_ADVICE = (
    "Svara med det registret säger (search_register) och säg till användaren att {whose} "
    "dokument inte är inlästa, i stället för att svara att det inte framgår eller citera andra "
    "avtals dokument."
)


def mention(
    clause: Clause,
    section: int,
    raw: str,
    kind: ReferenceKind,
    key: str,
    status: ReferenceStatus | None = None,
) -> ReferenceMention:
    """A mention of `raw` in the clause's text, at its first place there."""
    heading, paragraphs, _ = clause
    start = "\n\n".join((heading, *paragraphs)).index(raw)
    return ReferenceMention(
        section=section,
        start=start,
        end=start + len(raw),
        raw=raw,
        kind=kind,
        key=key,
        rule="R1",
        status=status,
    )


# Advania's 1.10.4 names the agreement itself and, as a whole file, the general terms.
SELF_REFERENCE = Reference(
    sha256=ADVANIA_CARD,
    mention=mention(PRICE_ADJUSTMENT, 0, "Ramavtalet", ReferenceKind.DOCUMENT, "Ramavtalet"),
    status=ReferenceStatus.SELF,
    rule="R2",
)
TERMS_REFERENCE = Reference(
    sha256=ADVANIA_CARD,
    mention=mention(
        PRICE_ADJUSTMENT,
        0,
        "bilaga Allmänna villkor",
        ReferenceKind.ANNEX_NAME,
        "Allmänna villkor",
    ),
    status=ReferenceStatus.RESOLVED,
    rule="R5",
    targets=(ReferenceTarget(sha256=TERMS, section=None, page_url=None),),
)
# The general terms' 6.21.4 points to a section title that is not in the corpus; stored as
# if the resolver had offered 6.21.2.4 of both copies of the terms, one of them held back.
TERMINATION_REFERENCE = Reference(
    sha256=TERMS,
    mention=mention(
        PENALTY,
        1,
        "avsnitt Avropsberättigads uppsägningsrätt",
        ReferenceKind.SECTION_TITLE,
        "Avropsberättigads uppsägningsrätt",
    ),
    status=ReferenceStatus.AMBIGUOUS,
    rule="R4-llm",
    targets=(
        ReferenceTarget(sha256=TERMS, section=0, page_url=None),
        ReferenceTarget(sha256=HELD.sha256, section=HELD.section_position, page_url=None),
    ),
)
LAW_REFERENCE = Reference(
    sha256=TEMPLATE,
    mention=mention(
        SECURITY_END,
        0,
        "5 kap. 2 § säkerhetsskyddslagen",
        ReferenceKind.LAW,
        "5 kap. 2 § säkerhetsskyddslagen",
        ReferenceStatus.EXTERNAL,
    ),
    status=ReferenceStatus.EXTERNAL,
    rule=None,
)
REFERENCES = [SELF_REFERENCE, TERMS_REFERENCE, TERMINATION_REFERENCE, LAW_REFERENCE]

TERMS_FILE = TargetRef(
    sha256=TERMS,
    file_title="Allmänna villkor",
    document_type="general_terms",
    page_titles=[IT_DRIFT_TITLE],
    section_position=None,
    section_number=None,
    section_title=None,
    page_start=None,
    page_end=None,
)
TERMINATION_IN_TERMS = TargetRef(
    sha256=TERMS,
    file_title="Allmänna villkor",
    document_type="general_terms",
    page_titles=[IT_DRIFT_TITLE],
    section_position=0,
    section_number="6.21.2.4",
    section_title="Fel som utgör väsentligt avtalsbrott",
    page_start=24,
    page_end=24,
)
SELF_FOUND = SectionReference(
    raw="Ramavtalet", kind="document", status="self", targets=[], held_back_targets=0
)
TERMS_FOUND = SectionReference(
    raw="bilaga Allmänna villkor",
    kind="annex_name",
    status="resolved",
    targets=[TERMS_FILE],
    held_back_targets=0,
)
TERMINATION_FOUND = SectionReference(
    raw="avsnitt Avropsberättigads uppsägningsrätt",
    kind="section_title",
    status="ambiguous",
    targets=[TERMINATION_IN_TERMS],
    held_back_targets=1,
)
PRICE_ADJUSTMENT_IN_ADVANIA = SectionRef(
    sha256=ADVANIA_CARD,
    file_title="Ramavtal",
    document_type="supplier_agreement",
    page_titles=[IT_DRIFT_TITLE],
    section_position=0,
    section_number="1.10.4",
    section_title="Prisjustering",
    page_start=14,
    page_end=14,
)


@pytest.fixture
def indexed(engine: Engine) -> Engine:
    """The corpus with its references, as `process` stores it, and its index."""
    store_corpus(engine)
    with session_factory(engine).begin() as session:
        save_extraction(
            session,
            [DocumentExtraction(metadata=metadata(f), facts=(), mentions=()) for f in CORPUS],
            REFERENCES,
        )
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


@pytest.fixture
def whole_register(indexed: Engine) -> Engine:
    """The register loaded again with an area the corpus has no files for, as the real one."""
    with session_factory(indexed).begin() as session:
        load_register(session, REGISTER_VERSION, [*REGISTER, FURNITURE_ROW])
    return indexed


def stored_text(engine: Engine, sha256: str, position: int) -> str:
    with session_factory(engine)() as session:
        section = session.get(models.DocumentSection, (sha256, position))
        assert section is not None
        return section.text


# --- search_documents ---------------------------------------------------------------------------


def test_the_search_finds_the_penalty_in_the_terms_with_its_copy(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        result = search_documents(
            session, TopicEmbedder(), "Hur stort är vitet vid avtalsbrott?", limit=3
        )

    penalty, termination, _ = result.hits
    assert penalty.model_dump(exclude={"snippet", "score"}) == {
        "sha256": TERMS,
        "file_title": "Allmänna villkor",
        "document_type": "general_terms",
        "page_titles": [IT_DRIFT_TITLE],
        "section_position": 1,
        "section_number": "6.21.4",
        "section_title": "Ansvar och vite vid övriga avtalsbrott",
        "page_start": 24,
        "page_end": 24,
        "framework_areas": ["IT-drift"],
        "agreement_numbers": [],
        "path": ["6.21.4 Ansvar och vite vid övriga avtalsbrott"],
        "vector_rank": 1,
        "text_rank": 1,
        # The procurement document prints the same 6.21.4.
        "copies": [
            SectionCopy(
                sha256=PROCUREMENT,
                file_title="Upphandlingsdokument",
                section_position=1,
                section_number="6.21.4",
            ).model_dump()
        ],
    }
    assert penalty.snippet.startswith("6.21.4 Ansvar och vite vid övriga avtalsbrott\n\nVitets")
    assert penalty.score == pytest.approx(2 / (get_settings().rrf_k + 1))
    # 6.21.2.4's copy in the procurement document is held back, so it is not listed.
    assert (termination.sha256, termination.section_number, termination.copies) == (
        TERMS,
        "6.21.2.4",
        [],
    )


@pytest.mark.parametrize(
    ("filters", "first", "files"),
    [
        # In another case than the register's: the tool uses the register's spelling.
        ({"framework_area": "bemanningstjänster"}, (MAIN, 0), {MAIN, TEMPLATE}),
        # Advania's number written another way: its card and the procurement's shared files.
        (
            {"agreement_number": "23.3.5890-23-003"},
            (ADVANIA_CARD, 0),
            {TERMS, PROCUREMENT, ADVANIA_CARD, TEMPLATE},
        ),
        # A procurement number (no supplier's sequence): every file of the procurement.
        ({"agreement_number": "23.3-14537-2023"}, (MAIN, 0), {MAIN, TEMPLATE}),
        (
            {"document_type": DocumentType.SUPPLIER_AGREEMENT},
            (ADVANIA_CARD, 0),
            {ADVANIA_CARD, NETBIN_CARD},
        ),
    ],
    ids=["area", "agreement", "procurement", "document type"],
)
def test_the_search_keeps_to_its_filters(
    sessions: sessionmaker[Session],
    filters: dict[str, Any],
    first: tuple[str, int],
    files: set[str],
) -> None:
    with sessions() as session:
        result = search_documents(
            session, TopicEmbedder(), "Vad gäller vid prisjustering?", **filters
        )

    assert (result.hits[0].sha256, result.hits[0].section_position) == first
    assert result.hits[0].section_number == "1.10.4"
    assert {hit.sha256 for hit in result.hits} <= files


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (
            {"agreement_number": "23.3-9999-2023-001"},
            "finns inte i registret, varken som avtal eller som upphandling. Sök avtalet",
        ),
        (
            {"framework_area": "Möbler"},
            "'Möbler' finns inte i registret.\nOmråden i registret: Bemanningstjänster, IT-drift.",
        ),
    ],
    ids=["agreement", "area"],
)
def test_a_filter_the_register_does_not_know_is_an_error(
    sessions: sessionmaker[Session], filters: dict[str, Any], message: str
) -> None:
    with sessions() as session, pytest.raises(NotFoundError, match=message):
        search_documents(session, TopicEmbedder(), "Hur stort är vitet?", **filters)


# --- read_section -------------------------------------------------------------------------------


def test_a_section_is_read_whole_by_its_number_with_its_references(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    with sessions() as session:
        section = read_section(session, TERMS, section_number="6.21.4")
        # Spaces around it and a final dot are not part of the number.
        again = read_section(session, TERMS, section_number=" 6.21.4. ")

    text = stored_text(indexed, TERMS, 1)
    assert text.startswith("6.21.4 Ansvar och vite vid övriga avtalsbrott\n\nVitets storlek")
    assert (
        section
        == again
        == Section(
            sha256=TERMS,
            file_title="Allmänna villkor",
            document_type="general_terms",
            page_titles=[IT_DRIFT_TITLE],
            section_position=1,
            section_number="6.21.4",
            section_title="Ansvar och vite vid övriga avtalsbrott",
            page_start=24,
            page_end=24,
            path=["6.21.4 Ansvar och vite vid övriga avtalsbrott"],
            text=text,
            # The held-back candidate in the procurement document is counted, not shown.
            references=[TERMINATION_FOUND],
            held_back_targets=1,
        )
    )


def test_a_section_is_read_by_its_position(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        section = read_section(session, TEMPLATE, section_position=0)

    assert (section.section_number, section.section_title) == (
        "18",
        "Åtgärder när Säkerhetsskyddsavtalet upphör att gälla",
    )
    assert (section.file_title, section.document_type) == (
        "Utkast till Säkerhetsskyddsavtal",
        "template",
    )
    assert section.page_titles == [BEMANNING_TITLE, IT_DRIFT_TITLE]
    assert (section.page_start, section.page_end) == (9, 9)
    assert section.references == [
        SectionReference(
            raw="5 kap. 2 § säkerhetsskyddslagen",
            kind="law",
            status="external",
            targets=[],
            held_back_targets=0,
        )
    ]
    assert section.held_back_targets == 0


@pytest.mark.parametrize(
    "section",
    [
        {"section_number": "6.21.2.4"},
        {"section_position": HELD.section_position},
    ],
    ids=["number", "position"],
)
def test_a_held_back_section_cannot_be_read(
    sessions: sessionmaker[Session], section: dict[str, Any]
) -> None:
    with sessions() as session:
        with pytest.raises(NotFoundError, match="hålls tillbaka i granskningen"):
            read_section(session, PROCUREMENT, **section)
        # The rest of the file can be read.
        assert read_section(session, PROCUREMENT, section_number="6.21.4").section_position == 1


@pytest.mark.parametrize(
    ("sha256", "section", "message"),
    [
        (TERMS, {"section_number": "9.9"}, "inget avsnitt med nummer 9.9 .* get_outline"),
        (TERMS, {"section_position": 7}, "inget avsnitt på plats 7 .* get_outline"),
        (UNKNOWN_FILE, {"section_number": "6.21.4"}, "inget dokument med sha256 0{64}"),
    ],
    ids=["number", "position", "file"],
)
def test_a_section_that_is_not_there_says_where_to_look(
    sessions: sessionmaker[Session], sha256: str, section: dict[str, Any], message: str
) -> None:
    with sessions() as session, pytest.raises(NotFoundError, match=message):
        read_section(session, sha256, **section)


def test_a_number_two_shown_sections_share_is_ambiguous(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # As in a document that numbers two sections alike: both copies of the terms number
    # their 6.21.2.4 "6.21.4" too. In the procurement document the other one is held back.
    with session_factory(indexed).begin() as session:
        session.execute(
            update(models.DocumentSection)
            .where(models.DocumentSection.position == 0)
            .where(models.DocumentSection.sha256.in_([TERMS, PROCUREMENT]))
            .values(number="6.21.4")
        )

    with sessions() as session:
        with pytest.raises(
            AmbiguousError,
            match=(
                r"Numret 6\.21\.4 finns på flera avsnitt .*: plats 0 \(Fel som utgör väsentligt "
                r"avtalsbrott\), plats 1 \(Ansvar och vite vid övriga avtalsbrott\)\. "
                "Ange section_position"
            ),
        ):
            read_section(session, TERMS, section_number="6.21.4")
        assert read_section(session, TERMS, section_position=0).section_number == "6.21.4"
        # The held-back section is not a candidate, so the number is not ambiguous here.
        assert read_section(session, PROCUREMENT, section_number="6.21.4").section_position == 1


def test_a_number_and_a_position_together_must_name_the_same_section(
    sessions: sessionmaker[Session],
) -> None:
    # A search hit gives both, and the model may pass both on.
    with sessions() as session:
        section = read_section(session, TERMS, section_number="6.21.4", section_position=1)
        with pytest.raises(
            AmbiguousError,
            match=(
                r"section_number 6\.21\.4 och section_position 0 är olika avsnitt .*: plats 0 "
                r"\(Fel som utgör väsentligt avtalsbrott\) har nummer 6\.21\.2\.4\. "
                "Ange bara ett av dem"
            ),
        ):
            read_section(session, TERMS, section_number="6.21.4", section_position=0)

    assert (section.section_position, section.section_number) == (1, "6.21.4")


# --- get_outline --------------------------------------------------------------------------------


def test_the_outline_lists_the_sections_in_order(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        outline = get_outline(session, TERMS)

    assert outline == Outline(
        sha256=TERMS,
        file_title="Allmänna villkor",
        document_type="general_terms",
        page_titles=[IT_DRIFT_TITLE],
        sections=[
            OutlineSection(
                section_position=0,
                section_number="6.21.2.4",
                section_title="Fel som utgör väsentligt avtalsbrott",
                level=4,
                page_start=24,
            ),
            OutlineSection(
                section_position=1,
                section_number="6.21.4",
                section_title="Ansvar och vite vid övriga avtalsbrott",
                level=3,
                page_start=24,
            ),
        ],
        held_back=0,
    )


def test_the_outline_leaves_out_a_held_back_section_and_counts_it(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        outline = get_outline(session, PROCUREMENT)

    assert (outline.file_title, outline.document_type) == (
        "Upphandlingsdokument",
        "procurement_document",
    )
    assert [(s.section_position, s.section_number) for s in outline.sections] == [(1, "6.21.4")]
    assert outline.held_back == 1


# --- resolve_reference --------------------------------------------------------------------------


def test_the_references_of_a_section_come_in_text_order(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        result = resolve_reference(session, ADVANIA_CARD, section_number="1.10.4")

    assert result.section == PRICE_ADJUSTMENT_IN_ADVANIA
    # "Ramavtalet" is in the first paragraph; the whole terms file is the other's target.
    assert result.references == [SELF_FOUND, TERMS_FOUND]
    assert result.held_back_targets == 0


@pytest.mark.parametrize(
    ("reference", "found"),
    [("ALLMÄNNA villkor", [TERMS_FOUND]), ("ramavtal", [SELF_FOUND]), ("punkt 6.21.9", [])],
    ids=["terms", "self", "none"],
)
def test_a_reference_is_picked_by_part_of_its_text_in_any_case(
    sessions: sessionmaker[Session], reference: str, found: list[SectionReference]
) -> None:
    with sessions() as session:
        result = resolve_reference(session, ADVANIA_CARD, section_position=0, reference=reference)

    assert result.references == found


def test_a_target_in_a_held_back_section_is_left_out_and_counted(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        result = resolve_reference(session, TERMS, section_number="6.21.4", reference="avsnitt")

    assert result.references == [TERMINATION_FOUND]
    assert result.held_back_targets == 1
    with sessions() as session, pytest.raises(NotFoundError, match="hålls tillbaka"):
        resolve_reference(session, PROCUREMENT, section_position=HELD.section_position)


def test_a_target_found_through_two_pages_is_listed_or_counted_once(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # As step 4 stores targets once per agreement page when the pages disagree: both
    # candidates again, found through another page; the second one is held back.
    with session_factory(indexed).begin() as session:
        reference_id = session.scalar(
            select(models.DocumentReference.id).where(
                models.DocumentReference.raw == TERMINATION_FOUND.raw
            )
        )
        session.execute(
            insert(models.ReferenceTarget),
            [
                {
                    "reference_id": reference_id,
                    "position": position,
                    "target_sha256": sha256,
                    "target_section_position": section,
                    "page_url": OTHER_PAGE,
                }
                for position, (sha256, section) in enumerate(
                    [(TERMS, 0), (HELD.sha256, HELD.section_position)], start=2
                )
            ],
        )

    with sessions() as session:
        result = resolve_reference(session, TERMS, section_number="6.21.4", reference="avsnitt")
        section = read_section(session, TERMS, section_number="6.21.4")

    assert result.references == section.references == [TERMINATION_FOUND]
    assert result.held_back_targets == section.held_back_targets == 1


# --- what the tools show when the ingestion holds something back ---------------------------------


def test_a_file_held_back_after_the_index_was_built_is_shown_by_no_tool(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # The index still has their chunks (no `index` since); the tools check the findings.
    held = [
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject="6.21.2.4",
            message="Avsnitt 6.21.2.4 ligger på en skannad sida utan text.",
            sha256=PROCUREMENT,
            section=HELD.section_position,
        ),
        *(
            Finding(
                check="still_published",
                severity=Severity.QUARANTINE,
                subject=sha256,
                message="Ingen avtalssida på avropa.se länkar längre till filen.",
                sha256=sha256,
            )
            for sha256 in (TEMPLATE, NETBIN_CARD)
        ),
    ]
    with session_factory(indexed).begin() as session:
        save_findings(session, held)

    with sessions() as session:
        security = search_documents(
            session, TopicEmbedder(), "Vilken tystnadsplikt följer av säkerhetsskyddslagen?"
        )
        prices = search_documents(session, TopicEmbedder(), "Vad gäller vid prisjustering?")
        with pytest.raises(NotFoundError, match="hålls tillbaka i granskningen"):
            read_section(session, TEMPLATE, section_number="18")
        with pytest.raises(NotFoundError, match="hålls tillbaka i granskningen"):
            get_outline(session, NETBIN_CARD)

    assert {hit.sha256 for hit in security.hits + prices.hits}.isdisjoint({TEMPLATE, NETBIN_CARD})
    first = prices.hits[0]
    assert (first.sha256, first.section_position) == (ADVANIA_CARD, 0)
    assert [(copy.sha256, copy.section_position) for copy in first.copies] == [(MAIN, 0)]


def test_between_process_and_index_the_tools_show_nothing(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # As after `process`: the sections were stored again and the index emptied.
    with session_factory(indexed).begin() as session:
        clear_index(session)

    with sessions() as session:
        with pytest.raises(UnavailableError, match="Sökindexet är inte byggt"):
            search_documents(session, TopicEmbedder(), "Hur stort är vitet?")
        with pytest.raises(NotFoundError, match="är inte indexerat"):
            read_section(session, TERMS, section_number="6.21.4")
        with pytest.raises(NotFoundError, match="är inte indexerat"):
            get_outline(session, TERMS)
        with pytest.raises(NotFoundError, match="är inte indexerat"):
            resolve_reference(session, ADVANIA_CARD, section_number="1.10.4")


# --- a filter outside the pilot -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (
            {"framework_area": "möbler och inredning"},
            "Ramavtalsområdet Möbler och inredning finns i registret, men områdets dokument är "
            "inte inlästa.\nOmråden med inlästa dokument: Bemanningstjänster, IT-drift. "
            + NOT_LOADED_ADVICE.format(whose="områdets"),
        ),
        (
            {"agreement_number": "23.3.10777-24-002"},  # written another way
            f"Avtalet {FURNITURE} finns i registret, men avtalets dokument är inte inlästa.\n"
            "Områden med inlästa dokument: Bemanningstjänster, IT-drift. "
            + NOT_LOADED_ADVICE.format(whose="avtalets"),
        ),
        (
            {"agreement_number": FURNITURE_PROCUREMENT},
            f"Upphandlingen {FURNITURE_PROCUREMENT} finns i registret, men upphandlingens "
            "dokument är inte inlästa.\nOmråden med inlästa dokument: Bemanningstjänster, "
            "IT-drift. " + NOT_LOADED_ADVICE.format(whose="upphandlingens"),
        ),
        # IT-drift has documents and the agreement has none: the error is about the agreement.
        (
            {"framework_area": "IT-drift", "agreement_number": FURNITURE},
            f"Avtalet {FURNITURE} finns i registret, men avtalets dokument är inte inlästa.\n",
        ),
        # With a document type it is still not an empty answer.
        (
            {"agreement_number": FURNITURE, "document_type": DocumentType.GENERAL_TERMS},
            f"Avtalet {FURNITURE} finns i registret, men avtalets dokument är inte inlästa.\n",
        ),
    ],
    ids=["area", "agreement", "procurement", "loaded area and agreement", "agreement and type"],
)
def test_a_filter_outside_the_pilot_says_that_its_documents_are_not_loaded(
    whole_register: Engine,
    sessions: sessionmaker[Session],
    filters: dict[str, Any],
    message: str,
) -> None:
    with sessions() as session:
        with pytest.raises(NotFoundError, match=f"^{re.escape(message)}"):
            search_documents(session, TopicEmbedder(), "Hur stort är vitet?", **filters)
        with pytest.raises(NotFoundError, match=f"^{re.escape(message)}"):
            list_documents(session, **filters)


@pytest.mark.parametrize(
    ("filters", "files"),
    [
        ({"framework_area": "IT-drift"}, [TERMS, ADVANIA_CARD, NETBIN_CARD, PROCUREMENT, TEMPLATE]),
        # A Hub has no card of its own: its procurement's shared files are its documents.
        ({"agreement_number": A_HUB}, [MAIN, TEMPLATE]),
        # A type the agreement has no documents of is an empty answer, not an error.
        ({"agreement_number": A_HUB, "document_type": DocumentType.GENERAL_TERMS}, []),
    ],
    ids=["pilot area", "pilot agreement with shared files only", "type without documents"],
)
def test_a_filter_inside_the_pilot_answers_as_before(
    whole_register: Engine,
    sessions: sessionmaker[Session],
    filters: dict[str, Any],
    files: list[str],
) -> None:
    with sessions() as session:
        result = search_documents(
            session, TopicEmbedder(), "Vad gäller vid prisjustering?", **filters
        )
        listed = list_documents(session, **filters)

    assert [document.sha256 for document in listed.documents] == files
    assert {hit.sha256 for hit in result.hits} <= set(files)
    assert bool(result.hits) == bool(files)


def test_an_area_whose_files_are_all_held_back_counts_as_not_loaded(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    # As the tools show it: after the index was built, Bemanningstjänster's two files are held
    # back whole, so A Hub's shared files are too.
    with session_factory(indexed).begin() as session:
        save_findings(
            session,
            [
                Finding(
                    check="still_published",
                    severity=Severity.QUARANTINE,
                    subject=sha256,
                    message="Ingen avtalssida på avropa.se länkar längre till filen.",
                    sha256=sha256,
                )
                for sha256 in (MAIN, TEMPLATE)
            ],
        )

    with sessions() as session:
        with pytest.raises(
            NotFoundError,
            match=re.escape(
                "Ramavtalsområdet Bemanningstjänster finns i registret, men områdets dokument är "
                "inte inlästa.\nOmråden med inlästa dokument: IT-drift. "
            ),
        ):
            list_documents(session, framework_area="Bemanningstjänster")
        with pytest.raises(NotFoundError, match=f"^Avtalet {A_HUB} finns i registret, men"):
            search_documents(
                session, TopicEmbedder(), "Vad gäller vid prisjustering?", agreement_number=A_HUB
            )
        it_drift = list_documents(session, framework_area="IT-drift")

    assert it_drift.total == 4  # its other files are still listed


@pytest.mark.parametrize(
    "filters",
    [
        {"framework_area": FURNITURE_AREA},
        {"agreement_number": FURNITURE},
        {"framework_area": "IT-drift"},
    ],
    ids=["area outside", "agreement outside", "pilot area"],
)
def test_without_an_index_no_filter_is_called_not_loaded(
    whole_register: Engine, sessions: sessionmaker[Session], filters: dict[str, Any]
) -> None:
    # As after `process`: no file is shown, so both tools say that the index is missing.
    with session_factory(whole_register).begin() as session:
        clear_index(session)

    with sessions() as session:
        with pytest.raises(UnavailableError, match="Sökindexet är inte byggt"):
            search_documents(session, TopicEmbedder(), "Hur stort är vitet?", **filters)
        with pytest.raises(UnavailableError, match="Sökindexet är inte byggt"):
            list_documents(session, **filters)


# --- the server's engine --------------------------------------------------------------------------


def test_the_servers_engine_refuses_every_write(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    with sessions() as session:
        isolation = session.connection().get_isolation_level()
        with pytest.raises(DBAPIError, match="read-only transaction") as refused:
            session.execute(delete(models.ValidationFinding))

    assert isolation == "REPEATABLE READ"  # one snapshot per tool call
    assert isinstance(refused.value.orig, psycopg.errors.ReadOnlySqlTransaction)
    with session_factory(indexed)() as session:  # the finding that holds 6.21.2.4 is still there
        assert session.scalar(select(func.count()).select_from(models.ValidationFinding)) == 1


# --- through MCP --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_each_tool_answers_through_mcp_with_structured_content(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    server = build_server(sessions, TopicEmbedder(), allowed_hosts=["localhost:*"])
    async with create_connected_server_and_client_session(server) as client:
        search = await client.call_tool(
            "search_documents",
            {"query": "Hur stort är vitet?", "document_type": "general_terms", "limit": 2},
        )
        section = await client.call_tool(
            "read_section", {"sha256": TERMS, "section_number": "6.21.4"}
        )
        outline = await client.call_tool("get_outline", {"sha256": PROCUREMENT})
        references = await client.call_tool(
            "resolve_reference",
            {"sha256": ADVANIA_CARD, "section_position": 0, "reference": "allmänna villkor"},
        )
        held = await client.call_tool(
            "read_section", {"sha256": PROCUREMENT, "section_position": HELD.section_position}
        )

    for result in (search, section, outline, references):
        assert result.isError is False
        assert result.structuredContent is not None
    assert search.structuredContent is not None  # for mypy, as below
    hits = search.structuredContent["hits"]
    assert [(hit["sha256"], hit["section_number"]) for hit in hits] == [
        (TERMS, "6.21.4"),
        (TERMS, "6.21.2.4"),
    ]
    assert hits[0]["copies"] == []  # the procurement document's copy is not general terms
    assert section.structuredContent is not None
    assert section.structuredContent["text"] == stored_text(indexed, TERMS, 1)
    assert section.structuredContent["references"] == [TERMINATION_FOUND.model_dump(mode="json")]
    assert outline.structuredContent is not None
    assert outline.structuredContent["held_back"] == 1
    assert [s["section_number"] for s in outline.structuredContent["sections"]] == ["6.21.4"]
    assert references.structuredContent is not None
    assert references.structuredContent == {
        "section": PRICE_ADJUSTMENT_IN_ADVANIA.model_dump(mode="json"),
        "references": [TERMS_FOUND.model_dump(mode="json")],
        "held_back_targets": 0,
    }
    assert held.isError is True
    assert held.structuredContent is None
    assert isinstance(held.content[0], TextContent)
    assert "hålls tillbaka i granskningen" in held.content[0].text
