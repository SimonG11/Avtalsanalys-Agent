"""Integration test: the extraction tables (ingestion/extraction_store.py) against a real Postgres.

What:
    Loads the sample register and a catalog with two files, stores their
    sections, then checks what steps 4 and 5 read back (links, also the date
    a page stopped being listed, register entries, register version), that
    an extraction with references, targets and findings is stored and
    replaced, and which files and sections `quarantine` holds back,
    including a file that step 3 stored without steps 4 and 5.

Why:
    The joins, the foreign keys between references and sections, and the
    fail-closed quarantine are SQL that unit tests cannot cover.

How:
    Uses the `engine` fixture from conftest.py. The sections are split by
    step 3 itself. The reference text is a real line,
    69efbee998c7 §3.1.2: "Se Kravkatalog avsnitt 8.1.38 Miljö och hållbarhet,
    enligt punkt 3.1.1 ovan." The org number of the supplier is made up.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError

from avtalsagent.db import models
from avtalsagent.db.session import session_factory
from avtalsagent.domain.documents import AgreementPage, CatalogLink, DocumentLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Fact,
    FactKind,
    FactRole,
    Finding,
    Quarantine,
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
    Severity,
)
from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.catalog import save_fetch
from avtalsagent.ingestion.extraction_store import (
    catalog_links,
    quarantine,
    register_entries,
    register_version,
    save_extraction,
    save_findings,
)
from avtalsagent.ingestion.section_store import document_links, save_sections
from avtalsagent.ingestion.step1_fetch import FetchResult, FetchStatus, StoredDocument
from avtalsagent.ingestion.step3_chunk import chunk_document, document_context
from avtalsagent.register.load import load_register
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register

REPORT = "a" * 64  # "Redovisning av hållbarhetskrav"
CATALOGUE = "b" * 64  # "Kravkatalog"
NOT_PARSED = "c" * 64  # a file whose parse failed: no parsed_file row
PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/x/bemanningstjanster/"
REPORT_URL = "https://www.avropa.se/globalassets/bilagor/redovisning-av-hallbarhetskrav.pdf"
CATALOGUE_URL = "https://www.avropa.se/globalassets/bilagor/kravkatalog.pdf"
PROCUREMENT = "23.3-14537-2023"  # in the sample register
SENTENCE = "Se Kravkatalog avsnitt 8.1.38 Miljö och hållbarhet, enligt punkt 3.1.1 ovan."


def link(url: str, title: str) -> DocumentLink:
    return DocumentLink(
        url=url,
        version="v1",
        title=title,
        category="Avtal",
        agreement_number=None,
        file_type="pdf",
        site_updated=date(2026, 8, 27),
    )


def parsed(sha256: str, *paragraphs: tuple[str, str]) -> ParsedDocument:
    blocks: list[Block] = []
    for heading, text in paragraphs:
        blocks.append(Block(kind=BlockKind.HEADING, text=heading, page=1))
        blocks.append(Block(kind=BlockKind.TEXT, text=text, page=1))
    return ParsedDocument(
        sha256=sha256, file_type="pdf", parser="test", pages=(), blocks=tuple(blocks)
    )


# Sections 0 and 1 of each file (no text before the first heading).
REPORT_DOCUMENT = parsed(
    REPORT,
    ("3.1.1 Miljökrav på upphandlingsföremålet", "Leverantören ska uppfylla miljökraven."),
    ("3.1.2 Social och etiska krav på upphandlingsföremålet", SENTENCE),
)
CATALOGUE_DOCUMENT = parsed(
    CATALOGUE,
    ("8.1.37 Kvalitetsledning", "Leverantören ska ha ett ledningssystem för kvalitet."),
    ("8.1.38 Miljö och hållbarhet", "Leverantören ska arbeta med miljöfrågor."),
)


def store_sections(engine: Engine) -> None:
    """Step 3 alone: replaces every parsed_file row, as `save_sections` always does."""
    factory = session_factory(engine)
    with factory() as session:
        links = document_links(session)
    documents = [REPORT_DOCUMENT, CATALOGUE_DOCUMENT]
    chunked = [chunk_document(d, document_context(links[d.sha256])) for d in documents]
    with factory.begin() as session:
        save_sections(session, documents, chunked)


@pytest.fixture
def stored(engine: Engine, sample_register_xlsx: Path) -> Engine:
    raw = read_register(sample_register_xlsx)
    report = link(REPORT_URL, "Redovisning av hållbarhetskrav")
    catalogue = link(CATALOGUE_URL, "Kravkatalog")
    page = AgreementPage(
        url=PAGE,
        title="Bemanningstjänster - IT-tjänster upp till 1000 timmar",
        procurement_numbers=(PROCUREMENT,),
        agreement_period="2025-04-03 - 2029-04-02",
        documents=(report, catalogue),
    )
    results = [
        FetchResult(item, FetchStatus.NEW, StoredDocument(item.url, "v1", sha, 1, f"{sha}.pdf"))
        for item, sha in ((report, REPORT), (catalogue, CATALOGUE))
    ]
    with session_factory(engine).begin() as session:
        session.execute(delete(models.ParsedFile))
        session.execute(delete(models.ValidationFinding))
        for model in (models.AgreementPageDocument, models.SourceDocument, models.AgreementPage):
            session.execute(delete(model))
        load_register(session, raw.version, normalize_rows(raw.rows).rows)
        save_fetch(session, [page], results, [PAGE])
    store_sections(engine)
    return engine


def metadata(
    sha256: str, document_type: DocumentType, type_rule: str, title: str
) -> DocumentMetadata:
    return DocumentMetadata(
        sha256=sha256,
        title=title,
        document_type=document_type,
        type_rule=type_rule,
        agreement_number=None,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=None,
        is_template=False,
        version_date=date(2023, 5, 2),
        version_rule="file_name",
        published_on=None,
        site_updated=date(2026, 8, 27),
    )


def mention(raw: str, kind: ReferenceKind, key: str, rule: str) -> ReferenceMention:
    start = SENTENCE.index(raw) + len("3.1.2 Social och etiska krav på upphandlingsföremålet\n\n")
    return ReferenceMention(
        section=1, start=start, end=start + len(raw), raw=raw, kind=kind, key=key, rule=rule
    )


CATALOGUE_FACTS = (
    Fact(
        kind=FactKind.PROCUREMENT_NUMBER,
        value=PROCUREMENT,
        raw="23.3-14537-2023",
        rule="PROC",
        block=1,
        page=1,
        role=FactRole.SELF,
    ),
    Fact(
        kind=FactKind.ORG_NUMBER,
        value="559900-0001",
        raw="559900-0001",
        rule="ORG",
        block=3,
        page=1,
    ),
    Fact(
        kind=FactKind.PERIOD_START,
        value="2025-04-03",
        raw="2025-04-03",
        rule="P6",
        block=3,
        page=1,
        scope="Kontorstjänster",
        statement=1,
    ),
)
EXTRACTIONS = [
    DocumentExtraction(
        metadata=metadata(
            REPORT, DocumentType.REQUIREMENTS_REPORT, "R09", "Redovisning av hållbarhetskrav"
        ),
        facts=(),
        mentions=(
            mention("Kravkatalog avsnitt 8.1.38", ReferenceKind.SECTION_NUMBER, "8.1.38", "R1x"),
            mention("punkt 3.1.1", ReferenceKind.SECTION_NUMBER, "3.1.1", "R1"),
        ),
    ),
    DocumentExtraction(
        metadata=metadata(CATALOGUE, DocumentType.REQUIREMENTS_CATALOGUE, "R12", "Kravkatalog"),
        facts=CATALOGUE_FACTS,
        mentions=(),
    ),
]
OTHER_FILE, SAME_FILE = EXTRACTIONS[0].mentions
REFERENCES = [
    Reference(
        sha256=REPORT,
        mention=OTHER_FILE,
        status=ReferenceStatus.RESOLVED,
        rule="R1x",
        targets=(ReferenceTarget(sha256=CATALOGUE, section=1, page_url=None),),
    ),
    Reference(
        sha256=REPORT,
        mention=SAME_FILE,
        status=ReferenceStatus.AMBIGUOUS,
        rule="R1",
        targets=(
            ReferenceTarget(sha256=REPORT, section=0, page_url=PAGE),
            ReferenceTarget(sha256=CATALOGUE, section=None, page_url=PAGE),
        ),
    ),
]


def test_catalog_and_register_are_read_back(stored: Engine, sample_register_xlsx: Path) -> None:
    raw = read_register(sample_register_xlsx)
    with session_factory(stored)() as session:
        links = catalog_links(session)
        entries = register_entries(session)
        version = register_version(session)

    assert links[0] == CatalogLink(
        sha256=CATALOGUE,
        url=CATALOGUE_URL,
        title="Kravkatalog",
        category="Avtal",
        agreement_number=None,
        site_updated=date(2026, 8, 27),
        page_url=PAGE,
        page_title="Bemanningstjänster - IT-tjänster upp till 1000 timmar",
        page_procurement_numbers=(PROCUREMENT,),
        page_period="2025-04-03 - 2029-04-02",
        page_missing_since=None,
    )
    assert [(item.sha256, item.title) for item in links] == [
        (CATALOGUE, "Kravkatalog"),
        (REPORT, "Redovisning av hållbarhetskrav"),
    ]
    # The sample has no repeated rows, so each row is one entry.
    assert set(entries) == {RegisterEntry.from_row(row) for row in normalize_rows(raw.rows).rows}
    assert len(entries) == len(set(entries))
    assert version == raw.version


def test_a_page_no_longer_listed_is_read_back_with_the_date_it_went(stored: Engine) -> None:
    # `fetch` marks the page (test_document_catalog.py); step 5's still_published reads it here.
    went = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    with session_factory(stored).begin() as session:
        session.execute(update(models.AgreementPage).values(missing_since=went))
    with session_factory(stored)() as session:
        links = catalog_links(session)

    assert [item.page_missing_since for item in links] == [went, went]


def test_saving_an_extraction_twice_replaces_it(stored: Engine) -> None:
    factory = session_factory(stored)
    with factory.begin() as session:
        save_extraction(session, EXTRACTIONS, REFERENCES)
    with factory() as session:
        targets = session.execute(
            select(
                models.ReferenceTarget.reference_id,
                models.ReferenceTarget.target_sha256,
                models.ReferenceTarget.target_section_position,
                models.ReferenceTarget.page_url,
            ).order_by(models.ReferenceTarget.reference_id, models.ReferenceTarget.position)
        ).all()
    assert [tuple(row) for row in targets] == [
        (1, CATALOGUE, 1, None),
        (2, REPORT, 0, PAGE),
        (2, CATALOGUE, None, PAGE),  # the whole document: no section to match
    ]

    # The second run finds only the reference to the catalogue.
    with factory.begin() as session:
        save_extraction(session, EXTRACTIONS, REFERENCES[:1])
    with factory() as session:
        files = session.scalars(
            select(models.DocumentMetadata).order_by(models.DocumentMetadata.sha256)
        ).all()
        facts = session.scalars(select(models.DocumentFact).order_by(models.DocumentFact.id)).all()
        references = session.scalars(select(models.DocumentReference)).all()
        target_count = len(session.scalars(select(models.ReferenceTarget)).all())

    assert [(f.sha256, f.document_type, f.document_group, f.binding) for f in files] == [
        (REPORT, "requirements_report", "support", False),
        (CATALOGUE, "requirements_catalogue", "agreement", True),
    ]
    assert [(f.sha256, f.kind, f.value, f.block_index, f.role, f.scope) for f in facts] == [
        (CATALOGUE, "procurement_number", PROCUREMENT, 1, "self", None),
        (CATALOGUE, "org_number", "559900-0001", 3, None, None),
        (CATALOGUE, "period_start", "2025-04-03", 3, None, "Kontorstjänster"),
    ]
    assert [
        (r.sha256, r.section_position, r.raw, r.pattern_rule, r.mention_status, r.status)
        for r in references
    ] == [(REPORT, 1, "Kravkatalog avsnitt 8.1.38", "R1x", None, "resolved")]
    assert target_count == 1


def test_quarantine_holds_files_and_sections_of_findings_not_accepted(stored: Engine) -> None:
    findings = [
        Finding(
            check="org_numbers",
            severity=Severity.QUARANTINE,
            subject="559900-0001",
            message="Organisationsnumret finns inte hos någon leverantör på avtalet.",
            sha256=CATALOGUE,
            evidence="559900-0001",
        ),
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject="3.1.2",
            message="Avsnittet ligger på en skannad sida utan text.",
            sha256=REPORT,
            section=1,
        ),
        Finding(
            check="procurement_number",
            severity=Severity.QUARANTINE,
            subject="23.3-9999-2023",
            message="Diarienumret hör inte till sidans upphandling.",
            sha256=REPORT,
            accepted_reason="Fel i sidhuvudet, rättat av SIC.",
        ),
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject="ingen text",
            message="Filen kunde inte läsas.",
            sha256=NOT_PARSED,
        ),
        Finding(
            check="coverage",
            severity=Severity.REPORT,
            subject="23.3-14537-2023-001",
            message="Avtalet täcks inte av något huvuddokument.",
            agreement_number="23.3-14537-2023-001",
        ),
        Finding(
            check="document_type",
            severity=Severity.NOTE,
            subject="F1",
            message="Typen kommer från en reservregel.",
            sha256=REPORT,
        ),
    ]
    factory = session_factory(stored)
    with factory.begin() as session:
        save_extraction(session, EXTRACTIONS, REFERENCES)
        save_findings(session, findings)
    with factory() as session:
        held = quarantine(session)
        stored_findings = session.scalars(
            select(models.ValidationFinding).order_by(models.ValidationFinding.id)
        ).all()

    assert held == Quarantine(
        files=frozenset({CATALOGUE, NOT_PARSED}),
        sections=frozenset({(REPORT, 1)}),
        unchecked=frozenset(),
    )
    assert held.holds(REPORT, 1)
    assert not held.holds(REPORT, 0)
    assert not held.holds(REPORT)
    assert [(f.check_name, f.severity, f.sha256, f.accepted_reason) for f in stored_findings] == [
        (f.check, f.severity.value, f.sha256, f.accepted_reason) for f in findings
    ]

    # The next run finds only the report and the note: its findings replace the old ones.
    with factory.begin() as session:
        save_findings(session, findings[4:])
    with factory() as session:
        assert quarantine(session) == Quarantine(frozenset(), frozenset(), frozenset())
        assert len(session.scalars(select(models.ValidationFinding)).all()) == 2


def test_quarantine_fails_closed_after_step_3_alone(stored: Engine) -> None:
    factory = session_factory(stored)
    with factory() as session:
        before = quarantine(session)  # the fixture ran step 3 alone
    with factory.begin() as session:
        save_extraction(session, EXTRACTIONS, REFERENCES)
        save_findings(session, [])
    with factory() as session:
        checked = quarantine(session)

    store_sections(stored)  # step 3 again, without steps 4 and 5
    with factory() as session:
        after = quarantine(session)
        files_left = session.scalars(select(models.DocumentMetadata.sha256)).all()

    assert before.unchecked == {REPORT, CATALOGUE}
    assert before.files == {REPORT, CATALOGUE}
    assert checked == Quarantine(frozenset(), frozenset(), frozenset())
    assert files_left == []  # cascaded away with the parsed_file rows
    assert after.unchecked == {REPORT, CATALOGUE}
    assert after.holds(REPORT, 0)


def test_a_quarantine_finding_must_name_its_file(stored: Engine) -> None:
    finding = Finding(
        check="coverage",
        severity=Severity.QUARANTINE,
        subject="23.3-14537-2023-001",
        message="Saknar fil.",
    )
    with pytest.raises(IntegrityError), session_factory(stored).begin() as session:
        save_findings(session, [finding])
