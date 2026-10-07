"""Integration test: the tool find_amendments of avtal-mcp against a real Postgres.

What:
    Runs `find_amendments` on the corpus of `test_index_store.py` with three
    files given other roles: Advania's card is an amendment of the general
    terms, NetBin's card a questions-and-answers log with three questions,
    and Bemanningstjänster's main document a second amendment, undated and
    on the IT-drift page, with its first section held back. References as
    step 4 stores them change the general terms' 6.21.4 and 6.21.2.4 and the
    whole file. Checks which references are amendments (`replaces`, from an
    amendment or a log, resolved or ambiguous), their order and dates, that
    one stored through two pages is listed once, that held-back amending
    sections and amended sections are counted and not shown, the errors for
    a missing or held-back target, that nothing is shown between `process`
    and `index`, and one call through the MCP SDK's in-memory client.

Why:
    The tool reads step 4's references backwards, through four tables and
    the visibility; which references a section or a file finds, and that the
    quarantine holds there too, is SQL that unit tests cannot cover.

How:
    Uses the `engine` fixture from conftest.py and `store_corpus`,
    `build_index` and `TopicEmbedder` of `test_index_store.py`, with that
    module's `CORPUS` replaced for the test (`monkeypatch`), so the new files
    go through steps 1-3 and 6 as the others do. The metadata and the
    references are stored as `process` stores them (`save_extraction`), and
    the findings with `save_findings`. The texts are invented in the form of
    the pilot's: a TendSign log (7a765d649e25 §9, bdf58b81d100 §122) and an
    amendment that gives a point new wording (386122e82b7b: "ändras härmed
    enligt följande"). The tool reads through a read-only engine, as the
    server does.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

import tests.integration.test_index_store as index_store_test
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
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
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.mcp_server.references import TargetRef
from avtalsagent.mcp_server.results import SectionRef
from avtalsagent.mcp_server.server import build_server
from avtalsagent.mcp_server.tools.find_amendments import (
    Amendment,
    AmendmentResult,
    excerpt,
    find_amendments,
)
from tests.integration.test_index_store import (
    ADVANIA_CARD,
    CORPUS,
    HELD,
    IT_DRIFT_PAGE,
    MAIN,
    NETBIN_CARD,
    PENALTY,
    PROCUREMENT,
    TERMS,
    Clause,
    CorpusFile,
    TopicEmbedder,
    build_index,
    metadata,
    store_corpus,
)

IT_DRIFT_TITLE = "IT-drift Mindre, upp till 200 anställda"
OTHER_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift/it-drift-storre/"
UNKNOWN_FILE = "0" * 64
AMENDMENT = ADVANIA_CARD  # Tillägg 1 till Allmänna villkor
LOG = NETBIN_CARD  # Frågor och svar
UNDATED = MAIN  # Tillägg 2 till Allmänna villkor

# --- The amending texts ------------------------------------------------------------------------

CHANGED_PENALTY: Clause = (
    "1 Ändring av punkt 6.21.4",
    (
        "Punkt 6.21.4 i Allmänna villkor ersätts med följande:",
        '"Vitets storlek uppgår till 30 000 SEK och utgår per påbörjad vecka som bristen '
        'kvarstår."',
    ),
    1,
)
ADDED_PARAGRAPH: Clause = (
    "2 Tillägg till Allmänna villkor",
    (
        "Följande stycke läggs till i Allmänna villkor:",
        '"Vite utgår inte för en brist som Avropsberättigad har orsakat."',
    ),
    1,
)
# A correction in the answer, the day after the question, which also names the section.
QUESTION_9: Clause = (
    "Publik fråga 9",
    (
        "Från:",
        "Dold",
        "Datum:",
        "Till:",
        "2024-02-19 13:35",
        "Alla",
        "I punkt 6.21.4 anges att vitets storlek uppgår till 25 000 SEK per påbörjad vecka. Vi "
        "föreslår att beloppet sänks till 10 000 SEK.",
        "Publikt svar",
        "Från:",
        "Statens inköpscentral vid Kammarkollegiet Datum:",
        "Till:",
        "2024-02-20 08:39",
        "Alla",
        'Rättelse. Texten som gäller är följande för punkt 6.21.4: "Vitets storlek uppgår till '
        '20 000 SEK."',
    ),
    3,
)
# The stamps before "Publikt svar", the answer's first, as in bdf58b81d100 §122.
QUESTION_10: Clause = (
    "Publik fråga 10",
    (
        "Från:",
        "Dold Datum:",
        "Till:",
        "Datum:",
        "Till:",
        "2024-03-05 16:27",
        "Alla",
        "2024-03-04 13:26",
        "Alla",
        "Kan den sista meningen förtydligas?",
        "Publikt svar",
        "Från:",
        "Statens inköpscentral vid Kammarkollegiet",
        "I punkt 6.21.4 stryks den sista meningen.",
    ),
    3,
)
QUESTION_11: Clause = (
    "Publik fråga 11",
    (
        "Från:",
        "Dold",
        "Datum:",
        "Till:",
        "2024-03-06 09:12",
        "Alla",
        "Gäller punkt 6.21.9 fortfarande?",
        "Publikt svar",
        "Från:",
        "Statens inköpscentral vid Kammarkollegiet Datum:",
        "Till:",
        "2024-03-07 10:08",
        "Alla",
        "Punkt 6.21.9 utgår helt.",
    ),
    3,
)
HELD_CHANGE: Clause = (
    "1 Ändring av punkt 6.21.4",
    ("Punkt 6.21.4 i Allmänna villkor ersätts med följande: Vite utgår inte.",),
    2,
)
REMOVED_TERMINATION: Clause = (
    "2 Ändring av punkt 6.21.2.4",
    ("Punkt 6.21.2.4 i Allmänna villkor utgår i sin helhet.",),
    2,
)

# Each file's new title, type and sections; all three linked from the IT-drift page alone.
NEW_ROLES: dict[str, tuple[str, DocumentType, tuple[Clause, ...]]] = {
    AMENDMENT: (
        "Tillägg 1 till Allmänna villkor",
        DocumentType.AMENDMENT,
        (CHANGED_PENALTY, ADDED_PARAGRAPH),
    ),
    LOG: (
        "Frågor och svar",
        DocumentType.QUESTIONS_AND_ANSWERS,
        (QUESTION_9, QUESTION_10, QUESTION_11),
    ),
    UNDATED: (
        "Tillägg 2 till Allmänna villkor",
        DocumentType.AMENDMENT,
        (HELD_CHANGE, REMOVED_TERMINATION),
    ),
}
AMENDED_CORPUS = tuple(
    replace(
        file,
        title=NEW_ROLES[file.sha256][0],
        pages=(IT_DRIFT_PAGE,),
        agreement_number=None,
        document_type=NEW_ROLES[file.sha256][1],
        clauses=NEW_ROLES[file.sha256][2],
    )
    if file.sha256 in NEW_ROLES
    else file
    for file in CORPUS
)
# The log's file date is later than its answers, which are dated by their own stamps.
DATES = {
    AMENDMENT: {"published_on": date(2025, 1, 15), "site_updated": date(2025, 6, 2)},
    LOG: {"site_updated": date(2025, 6, 2)},
}


def text_of(clause: Clause) -> str:
    """The section's text as step 3 stores it: the heading and the paragraphs."""
    heading, paragraphs, _ = clause
    return "\n\n".join((heading, *paragraphs))


def change(
    sha256: str,
    section: int,
    clause: Clause,
    raw: str,
    targets: list[tuple[str, int | None, str | None]],
    *,
    after: str = "\n",
    kind: ReferenceKind = ReferenceKind.SECTION_NUMBER,
    status: ReferenceStatus = ReferenceStatus.RESOLVED,
    replaces: bool = True,
) -> Reference:
    """A reference to `raw` in the clause, at its first place after `after` (the heading)."""
    text = text_of(clause)
    start = text.index(raw, text.index(after))
    return Reference(
        sha256=sha256,
        mention=ReferenceMention(
            section=section,
            start=start,
            end=start + len(raw),
            raw=raw,
            kind=kind,
            key=raw.split()[-1],
            rule="R1",
            replaces=replaces,
        ),
        status=status,
        rule="R1",
        targets=tuple(
            ReferenceTarget(sha256=target, section=position, page_url=page)
            for target, position, page in targets
        ),
    )


PENALTY_REPLACED = change(AMENDMENT, 0, CHANGED_PENALTY, "Punkt 6.21.4", [(TERMS, 1, None)])
PARAGRAPH_ADDED = change(
    AMENDMENT,
    1,
    ADDED_PARAGRAPH,
    "Allmänna villkor",
    [(TERMS, None, None)],
    after="Följande",
    kind=ReferenceKind.DOCUMENT,
)
PROPOSED = change(LOG, 0, QUESTION_9, "punkt 6.21.4", [(TERMS, 1, None)], replaces=False)
# Stored as if found through two agreement pages: listed once.
CORRECTED = change(
    LOG,
    0,
    QUESTION_9,
    "punkt 6.21.4",
    [(TERMS, 1, IT_DRIFT_PAGE), (TERMS, 1, OTHER_PAGE)],
    after="Rättelse",
)
STRUCK = change(
    LOG,
    1,
    QUESTION_10,
    "punkt 6.21.4",
    [(TERMS, 1, None), (PROCUREMENT, 1, None)],
    status=ReferenceStatus.AMBIGUOUS,
)
NOT_FOUND = change(
    LOG,
    2,
    QUESTION_11,
    "Punkt 6.21.9",
    [(TERMS, None, None)],
    status=ReferenceStatus.NUMBER_MISSING,
)
HELD_BACK = change(UNDATED, 0, HELD_CHANGE, "Punkt 6.21.4", [(TERMS, 1, None)])
TERMINATION_REMOVED = change(
    UNDATED,
    1,
    REMOVED_TERMINATION,
    "Punkt 6.21.2.4",
    [(TERMS, 0, None), (HELD.sha256, HELD.section_position, None)],
    status=ReferenceStatus.AMBIGUOUS,
)
# A procurement document's reference is no amendment, whatever step 4 marked.
NOT_AN_AMENDMENT = change(
    PROCUREMENT,
    1,
    PENALTY,
    "avsnitt Avropsberättigads uppsägningsrätt",
    [(TERMS, 1, None)],
    kind=ReferenceKind.SECTION_TITLE,
)
REFERENCES = [
    PENALTY_REPLACED,
    PARAGRAPH_ADDED,
    PROPOSED,
    CORRECTED,
    STRUCK,
    NOT_FOUND,
    HELD_BACK,
    TERMINATION_REMOVED,
    NOT_AN_AMENDMENT,
]
HELD_FINDINGS = [
    Finding(
        check="missing_text",
        severity=Severity.QUARANTINE,
        subject=subject,
        message=f"Avsnitt {subject} ligger på en skannad sida utan text.",
        sha256=sha256,
        section=section,
    )
    for sha256, section, subject in (
        (HELD.sha256, HELD.section_position, "6.21.2.4"),  # as store_corpus holds it
        (UNDATED, 0, "1"),
    )
]

# --- What the tool returns ---------------------------------------------------------------------


def file_of(sha256: str) -> CorpusFile:
    return next(file for file in AMENDED_CORPUS if file.sha256 == sha256)


def section_ref(sha256: str, position: int, number: str, title: str) -> SectionRef:
    file = file_of(sha256)
    page = file.clauses[position][2]
    return SectionRef(
        sha256=sha256,
        file_title=file.title,
        document_type=file.document_type.value,
        page_titles=[IT_DRIFT_TITLE],
        section_position=position,
        section_number=number,
        section_title=title,
        page_start=page,
        page_end=page,
    )


def target(ref: SectionRef) -> TargetRef:
    return TargetRef(**ref.model_dump())


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
PROCUREMENT_FILE = TERMS_FILE.model_copy(
    update={
        "sha256": PROCUREMENT,
        "file_title": "Upphandlingsdokument",
        "document_type": "procurement_document",
    }
)
TERMINATION_IN_TERMS = target(
    section_ref(TERMS, 0, "6.21.2.4", "Fel som utgör väsentligt avtalsbrott")
)
PENALTY_IN_TERMS = target(section_ref(TERMS, 1, "6.21.4", "Ansvar och vite vid övriga avtalsbrott"))
PENALTY_IN_PROCUREMENT = target(
    section_ref(PROCUREMENT, 1, "6.21.4", "Ansvar och vite vid övriga avtalsbrott")
)


def amendment(
    reference: Reference,
    clause: Clause,
    number: str,
    title: str,
    amended: TargetRef,
    dated: date | None,
) -> Amendment:
    mention = reference.mention
    return Amendment(
        amending=section_ref(reference.sha256, mention.section, number, title),
        amended=amended,
        raw=mention.raw,
        status=reference.status.value,
        dated=dated,
        excerpt=excerpt(text_of(clause), mention.start, mention.end),
    )


PENALTY_AMENDED = amendment(
    PENALTY_REPLACED,
    CHANGED_PENALTY,
    "1",
    "Ändring av punkt 6.21.4",
    PENALTY_IN_TERMS,
    date(2025, 1, 15),  # published_on: the amendment has no version date
)
TERMS_AMENDED = amendment(
    PARAGRAPH_ADDED,
    ADDED_PARAGRAPH,
    "2",
    "Tillägg till Allmänna villkor",
    TERMS_FILE,
    date(2025, 1, 15),
)
PENALTY_STRUCK = amendment(
    STRUCK, QUESTION_10, "10", "Publik fråga", PENALTY_IN_TERMS, date(2024, 3, 5)
)
PENALTY_CORRECTED = amendment(
    CORRECTED, QUESTION_9, "9", "Publik fråga", PENALTY_IN_TERMS, date(2024, 2, 20)
)
TERMINATION_AMENDED = amendment(
    TERMINATION_REMOVED,
    REMOVED_TERMINATION,
    "2",
    "Ändring av punkt 6.21.2.4",
    TERMINATION_IN_TERMS,
    None,
)


def dated_metadata(file: CorpusFile) -> DocumentMetadata:
    return metadata(file).model_copy(update=DATES.get(file.sha256, {}))


@pytest.fixture
def indexed(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """The corpus with the amendments and the log, as `process` and `index` store them."""
    monkeypatch.setattr(index_store_test, "CORPUS", AMENDED_CORPUS)
    store_corpus(engine)
    with session_factory(engine).begin() as session:
        save_extraction(
            session,
            [
                DocumentExtraction(metadata=dated_metadata(file), facts=(), mentions=())
                for file in AMENDED_CORPUS
            ],
            REFERENCES,
        )
        save_findings(session, HELD_FINDINGS)
    build_index(engine, TopicEmbedder())
    return engine


@pytest.fixture
def sessions(indexed: Engine) -> Iterator[sessionmaker[Session]]:
    """Sessions on an engine that refuses writes, as the server's does."""
    engine = create_db_engine(indexed.url.render_as_string(hide_password=False), read_only=True)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- A section ----------------------------------------------------------------------------------


def test_a_section_has_its_amendments_and_the_whole_files_newest_first(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        result = find_amendments(session, TERMS, section_number="6.21.4")

    assert result == AmendmentResult(
        target=PENALTY_IN_TERMS,
        amendments=[
            PENALTY_AMENDED,  # 2025-01-15, the section before the whole file
            TERMS_AMENDED,
            PENALTY_STRUCK,  # 2024-03-05: ambiguous, also listed for the procurement document
            PENALTY_CORRECTED,  # 2024-02-20, the answer's own stamp
        ],
        held_back=1,  # Tillägg 2's 1, held back by the quarantine
    )
    # Not the proposal in the question, the missing 6.21.9, the procurement document's
    # reference or 6.21.2.4's.
    assert result.amendments[0].excerpt == (
        "1 Ändring av punkt 6.21.4 Punkt 6.21.4 i Allmänna villkor ersätts med följande: "
        '"Vitets storlek uppgår till 30 000 SEK och utgår per påbörjad vecka som bristen '
        'kvarstår."'
    )
    corrected = result.amendments[3].excerpt
    assert corrected.startswith("…")
    assert corrected.endswith('"Vitets storlek uppgår till 20 000 SEK."')
    assert "Rättelse. Texten som gäller är följande för punkt 6.21.4" in corrected


def test_a_section_by_its_position_has_only_its_own_and_the_whole_files(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        result = find_amendments(session, TERMS, section_position=0)

    assert result == AmendmentResult(
        target=TERMINATION_IN_TERMS,
        amendments=[TERMS_AMENDED, TERMINATION_AMENDED],  # undated last
        held_back=0,  # the held-back amendment changes 6.21.4
    )


def test_an_ambiguous_amendment_is_listed_for_each_candidate(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        result = find_amendments(session, PROCUREMENT, section_number="6.21.4")

    assert result == AmendmentResult(
        target=PENALTY_IN_PROCUREMENT,
        amendments=[PENALTY_STRUCK.model_copy(update={"amended": PENALTY_IN_PROCUREMENT})],
        held_back=0,
    )


# --- A whole file -------------------------------------------------------------------------------


def test_a_file_has_the_amendments_of_all_its_sections(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        result = find_amendments(session, TERMS)

    assert result == AmendmentResult(
        target=TERMS_FILE,
        amendments=[
            PENALTY_AMENDED,
            TERMS_AMENDED,
            PENALTY_STRUCK,
            PENALTY_CORRECTED,
            TERMINATION_AMENDED,
        ],
        held_back=1,
    )


def test_an_amendment_of_a_held_back_section_is_counted_not_shown(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        result = find_amendments(session, PROCUREMENT)
        with pytest.raises(NotFoundError, match="hålls tillbaka i granskningen"):
            find_amendments(session, PROCUREMENT, section_position=HELD.section_position)

    # Tillägg 2's 2 also names the procurement document's 6.21.2.4, which is held back.
    assert result == AmendmentResult(
        target=PROCUREMENT_FILE,
        amendments=[PENALTY_STRUCK.model_copy(update={"amended": PENALTY_IN_PROCUREMENT})],
        held_back=1,
    )


def test_a_file_without_amendments_has_an_empty_list(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        result = find_amendments(session, AMENDMENT)

    assert (result.amendments, result.held_back) == ([], 0)


# --- Errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sha256", "section", "message"),
    [
        (TERMS, {"section_number": "9.9"}, "inget avsnitt med nummer 9.9 .* get_outline"),
        (UNKNOWN_FILE, {}, "inget dokument med sha256 0{64}"),
        (UNDATED, {"section_position": 0}, "hålls tillbaka i granskningen"),
    ],
    ids=["section", "file", "held back"],
)
def test_a_target_that_cannot_be_read_is_the_same_error_as_read_sections(
    sessions: sessionmaker[Session], sha256: str, section: dict[str, Any], message: str
) -> None:
    with sessions() as session, pytest.raises(NotFoundError, match=message):
        find_amendments(session, sha256, **section)


def test_between_process_and_index_the_tool_shows_nothing(
    indexed: Engine, sessions: sessionmaker[Session]
) -> None:
    with session_factory(indexed).begin() as session:
        clear_index(session)

    with sessions() as session:
        with pytest.raises(NotFoundError, match="är inte indexerat"):
            find_amendments(session, TERMS, section_number="6.21.4")
        with pytest.raises(NotFoundError, match="är inte indexerat"):
            find_amendments(session, TERMS)


# --- Through MCP --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_tool_answers_through_mcp_with_structured_content(
    sessions: sessionmaker[Session],
) -> None:
    server = build_server(sessions, None, allowed_hosts=["localhost:*"])
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(
            "find_amendments", {"sha256": TERMS, "section_number": "6.21.4"}
        )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["held_back"] == 1
    assert [found["dated"] for found in result.structuredContent["amendments"]] == [
        "2025-01-15",
        "2025-01-15",
        "2024-03-05",
        "2024-02-20",
    ]
    assert result.structuredContent["amendments"][2] == PENALTY_STRUCK.model_dump(mode="json")
