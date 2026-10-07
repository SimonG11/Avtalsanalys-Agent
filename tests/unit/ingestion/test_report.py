"""Tests for avtalsagent.ingestion.report.

The run is built by hand from pilot values, cited as sha[:12] and section or page:
the titles and types are those step 4 gives the files (0692da436391 "Ramavtalets
huvuddokument", 7a49e1a61b31 "Ramavtal" in the card of 23.3-2940-20:033,
e04bad6a0ced "Allmänna villkor" with pages 1 and 6-13 without a text layer and the
section "7.16 Prismodeller", 21dd4fde89d5 "Volymavtal" with no text, 19c85c74c3b2
"Nuts 2 indelning" typed by the fallback rule F1, 34d71a7e4da0 the TendSign printout
of Bemanningstjänster's main document). The findings' messages and evidence are those
of the pilot's checks (185c8246e536 p1 "23.3-1688-2024 IT-konsulttjänster -
IT-säkerhet"; the coverage of 6765/05 and of Bemanningstjänster). The pages are the
pilot's (34d71a7e4da0 on two of the four Bemanningstjänster pages, 65d611d12eab on
"IT-konsulttjänster 3. IT-säkerhet"). The references' texts are written as the pilot's
documents write them. Which agreement is covered how, the reviewer's name and the
"*utkast*" title are made up. 23.3-2940-20:018's card ee6107229c37 and the printout cbe12fd30683
of its sub-area are named, but not counted among the files of the run.
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Quarantine,
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
    Severity,
)
from avtalsagent.domain.parsed import Chunk, PageInfo, ParsedDocument, Section
from avtalsagent.domain.register import RegisterVersion
from avtalsagent.ingestion.checks.coverage import (
    NOT_COUNTED_NAMES,
    AgreementCoverage,
    CoverageStatus,
    NotCounted,
    NotCountedFile,
)
from avtalsagent.ingestion.checks.procurement_number import NumberStatus
from avtalsagent.ingestion.extract.reference_resolver import counts_in_rate, resolution_rate
from avtalsagent.ingestion.extract.title_matcher import MatchStats
from avtalsagent.ingestion.pipeline import IngestionResult
from avtalsagent.ingestion.report import (
    CHECK_NAMES,
    COVERAGE_STATUS_NAMES,
    DOCUMENT_TYPE_NAMES,
    NUMBER_STATUS_NAMES,
    OUTLINE_NAMES,
    REFERENCE_KIND_NAMES,
    REFERENCE_STATUS_NAMES,
    SEVERITY_NAMES,
    IngestionReport,
    RunInfo,
    build_report,
    render_json,
    render_markdown,
    render_report,
    write_report,
)
from avtalsagent.ingestion.step3_chunk import ChunkedDocument, OutlineKind
from avtalsagent.ingestion.step4_extract import CorpusExtraction
from avtalsagent.ingestion.step5_validate import DOCUMENT_CHECKS, AcceptedFinding, Validation

NBSP = " "


def sha(prefix: str) -> str:
    return prefix + "0" * (64 - len(prefix))


MAIN = sha("0692da436391")  # Ramavtalets huvuddokument, IT-konsulttjänster 1
CARD = sha("7a49e1a61b31")  # Ramavtal 23.3-2940-20:033
TERMS = sha("e04bad6a0ced")  # Allmänna villkor, Systemutveckling, partly scanned
TEMPLATE = sha("232f65cf161a")  # Avropsmall
DRAFT = sha("65d611d12eab")  # Ramavtalets huvuddokument with "[DATUM (dag-mån-år)]"
NO_TEXT = sha("21dd4fde89d5")  # Volymavtal (IBM), scanned
NUTS = sha("19c85c74c3b2")  # Nuts 2 indelning, typed by F1
PRICE = sha("185c8246e536")  # Prisbilaga - sammanställning Delområde 3
CALL = sha("54211e718d8e")  # Ansökningsinbjudan
PRINTOUT = sha("34d71a7e4da0")  # Ramavtalets huvuddokument of Bemanningstjänster, TendSign
MICROSOFT = sha("171a3cacf5fd")  # Volymavtalets huvudavtal 1.0
UNLINKED = sha("5c9b05f2cc79")
CARD_018 = sha("ee6107229c37")  # Ramavtal 23.3-2940-20:018, not among the files of the run
PRINTOUT_ITK2020 = sha("cbe12fd30683")  # the TendSign printout of 23.3-2940-20, likewise

AREAS = (
    "IT-drift",
    "Bemanningstjänster",
    "IT-konsulttjänster Resurskonsulter",
    "Programvaror och tjänster",
)
PAGE = (
    "https://www.avropa.se/ramavtal/ramavtalsomraden/konsulttjanster---bemanning-och-"
    "rekrytering/bemanningstjanster/bemanningstjanster---kontorstjanster-upp-till-1000-timmar/"
)
PAGE_TITLE = "Bemanningstjänster - Kontorstjänster upp till 1000 timmar"
START = datetime(2026, 10, 6, 14, 3, 12, tzinfo=timezone(timedelta(hours=2)))


# --- Building a run by hand ----------------------------------------------------------------


def metadata(
    sha256: str,
    title: str,
    kind: DocumentType,
    rule: str,
    *,
    agreement_number: str | None = None,
    is_template: bool = False,
    tendsign_cover: str | None = None,
) -> DocumentMetadata:
    return DocumentMetadata(
        sha256=sha256,
        title=title,
        document_type=kind,
        type_rule=rule,
        agreement_number=agreement_number,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=tendsign_cover,
        is_template=is_template,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )


def section(position: int, number: str | None, title: str) -> Section:
    return Section(
        position=position,
        number=number,
        title=title,
        level=number.count(".") + 1 if number else 0,
        parent=None,
        path=(),
        page_start=1,
        page_end=1,
        text=title,
    )


def chunks(count: int) -> list[Chunk]:
    return [Chunk(section=0, position=n, context_header="", text="") for n in range(count)]


# (metadata, file type, pages without a text layer of how many, outline, sections,
# chunks, numbers the table of contents lists without a section)
FILES = [
    (
        metadata(MAIN, "Ramavtalets huvuddokument", DocumentType.MAIN_DOCUMENT, "R05"),
        "pdf",
        ((), 24),
        OutlineKind.NUMBERED,
        [section(0, "1", "Inledning"), section(1, "1.10.2", "Ramavtalets omfattning")],
        3,
        [],
    ),
    (
        metadata(
            CARD,
            "Ramavtal",
            DocumentType.SUPPLIER_AGREEMENT,
            "R01",
            agreement_number="23.3-2940-20:033",
        ),
        "pdf",
        ((), 27),
        OutlineKind.NUMBERED,
        [section(0, "9.18.2", "Försäljningsredovisning och administrationsavgift")],
        1,
        None,
    ),
    (
        metadata(TERMS, "Allmänna villkor", DocumentType.GENERAL_TERMS, "R07"),
        "pdf",
        ((1, 6, 7, 8, 9, 10, 11, 12, 13), 31),
        OutlineKind.NUMBERED,
        [section(0, None, "Text före första rubriken"), section(1, "7.16", "Prismodeller")],
        2,
        ["7.17", "7.18"],
    ),
    (
        metadata(TEMPLATE, "Avropsmall", DocumentType.TEMPLATE, "R10", is_template=True),
        "docx",
        ((), 0),
        OutlineKind.HEADINGS,
        [section(0, None, "Avropsmall")],
        1,
        None,
    ),
    (
        metadata(
            DRAFT,
            "Ramavtalets huvuddokument",
            DocumentType.MAIN_DOCUMENT,
            "R05",
            is_template=True,
        ),
        "pdf",
        ((), 20),
        OutlineKind.NUMBERED,
        [section(0, "1", "Inledning")],
        1,
        None,
    ),
    (
        metadata(NO_TEXT, "Volymavtal", DocumentType.AMENDMENT, "R04"),
        "pdf",
        ((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), 10),
        OutlineKind.NONE,
        [],
        0,
        None,
    ),
    (
        metadata(NUTS, "Nuts 2 indelning", DocumentType.ANNEX, "F1"),
        "pdf",
        ((), 2),
        OutlineKind.NONE,
        [section(0, None, "Nuts 2 indelning")],
        1,
        None,
    ),
    (
        metadata(
            PRICE, "Prisbilaga - sammanställning Delområde 3", DocumentType.PRICE_ANNEX, "R08"
        ),
        "pdf",
        ((), 1),
        OutlineKind.NONE,
        [section(0, None, "Prisbilaga")],
        1,
        None,
    ),
    (
        metadata(CALL, "Ansökningsinbjudan", DocumentType.PROCUREMENT_DOCUMENT, "R03"),
        "pdf",
        ((), 40),
        OutlineKind.NUMBERED,
        [section(0, "1", "Inledning")],
        1,
        None,
    ),
    (
        metadata(
            PRINTOUT,
            "Ramavtalets huvuddokument",
            DocumentType.MAIN_DOCUMENT,
            "R05",
            is_template=True,
            tendsign_cover="Upphandlingsdokument",
        ),
        "pdf",
        ((), 24),
        OutlineKind.NUMBERED,
        [section(0, "1", "Inledning")],
        1,
        None,
    ),
    (
        metadata(MICROSOFT, "Volymavtalets huvudavtal 1.0", DocumentType.MAIN_DOCUMENT, "R05"),
        "pdf",
        ((), 6),
        OutlineKind.NUMBERED,
        [section(0, "1", "Definitioner")],
        1,
        None,
    ),
]


def mention(
    kind: ReferenceKind, raw: str, rule: str, status: ReferenceStatus | None = None
) -> ReferenceMention:
    return ReferenceMention(
        section=0, start=0, end=len(raw), raw=raw, kind=kind, key=raw, rule=rule, status=status
    )


def reference(
    kind: ReferenceKind, raw: str, status: ReferenceStatus, rule: str | None, count: int = 1
) -> list[Reference]:
    pattern = {ReferenceKind.LAW: "LAW", ReferenceKind.QUESTION: "RQ"}.get(kind, "R1")
    decided = status if rule is None else None
    item = Reference(
        sha256=MAIN, mention=mention(kind, raw, pattern, decided), status=status, rule=rule
    )
    return [item] * count


S, K = ReferenceStatus, ReferenceKind
REFERENCES = [
    *reference(K.SECTION_TITLE, "avsnitt Avtalsbrott och påföljder", S.RESOLVED, "R4", 3),
    *reference(K.SECTION_TITLE, "avsnitt Ansvar för Fel vid utförande", S.TITLE_MISSING, "R4"),
    *reference(
        K.SECTION_TITLE,
        "avsnitt Försäljningsredovisning och administrativ avgift",
        S.RESOLVED,
        "R4-llm",
    ),
    *reference(K.DOCUMENT, "Allmänna villkor", S.RESOLVED, "R2", 2),
    *reference(K.DOCUMENT, "Huvuddokumentet", S.SELF, "R2"),
    *reference(K.DOCUMENT, "Allmänna villkor gällande viten", S.RESOLVED, "R2-llm"),
    *reference(K.ANNEX_NAME, "bilaga Avropsberättigade", S.NOT_PUBLISHED, "R5", 2),
    *reference(K.SECTION_NUMBER, "punkt 6.21.9", S.RESOLVED, "R1"),
    *reference(K.SECTION_NUMBER, "punkterna 1-6 i detta avsnitt", S.LIST_ITEM, None),
    *reference(K.LAW, "17 kap. 17 § LOU", S.EXTERNAL, None, 2),
    *reference(K.ANNEX_NAME, "Bilaga nr x", S.PLACEHOLDER, None),
    *reference(K.DOCUMENT, "dessa Allmänna villkor", S.SELF, None),
    *reference(K.QUESTION, "fråga 1", S.RESOLVED, "RQ"),
    *reference(K.QUESTION, "fråga 31", S.NUMBER_MISSING, "RQ"),
    *reference(K.SECTION_NUMBER, "p. 6.20.11", S.AMBIGUOUS, "R1q"),
]

PROCUREMENT = Finding(
    check="procurement_number",
    severity=Severity.QUARANTINE,
    subject="23.3-1688-2024",
    message=(
        "Diarienumret 23.3-1688-2024 (s. 1) tillhör inte upphandlingen på sidan som länkar "
        "till dokumentet (23.3-8321-2024), och dokumentet anger inget eget nummer som gör "
        "det. I registret är det en annan upphandling inom IT-konsulttjänster Resurskonsulter."
    ),
    sha256=PRICE,
    evidence="23.3-1688-2024 IT-konsulttjänster - IT-säkerhet",
)
CITATION = Finding(
    check="procurement_number",
    severity=Severity.NOTE,
    subject="23.3-7067-2017",
    message=(
        "Dokumentet hänvisar inom parentes till diarienummer 23.3-7067-2017 (s. 2), som inte "
        "tillhör upphandlingen på sidorna som länkar till dokumentet (23.3-2940-20). Numret "
        "finns inte i registret."
    ),
    sha256=CALL,
    evidence="23.3-7067-17",
)
SCANNED = Finding(
    check="missing_text",
    severity=Severity.QUARANTINE,
    subject="7.16 Prismodeller",
    message="Avsnittet står på sidor utan textlager (s. 14-22), så dess text saknas.",
    sha256=TERMS,
    section=1,
)
ORG = Finding(
    check="org_numbers",
    severity=Severity.QUARANTINE,
    subject="502052-1307",
    message=(
        "Organisationsnumret 502052-1307 (s. 1), som dokumentet anger för Microsoft Ireland "
        "Operations Ltd, tillhör ingen leverantör på upphandlingen på sidan som länkar till "
        "dokumentet (23.5-3718-2024). Numret finns inte i registret."
    ),
    sha256=MICROSOFT,
    evidence="Microsoft Ireland Operations Ltd, organisationsnummer 502052-1307",
)
PAGE_PERIOD = Finding(
    check="agreement_period",
    severity=Severity.REPORT,
    subject="2025-04-22 - 2029-04-21",
    message=(
        "Sidans avtalsperiod 2025-04-22 - 2029-04-21 stämmer inte med registret, som har "
        "2025-04-03 - 2029-04-21 för delområdet Bemanningstjänster - Kontorstjänster upp till "
        "1000 timmar: 7 av 18 avtal har andra datum än sidan."
    ),
    page_url=PAGE,
    evidence="2025-04-22 - 2029-04-21",
)
UNCOVERED = Finding(
    check="coverage",
    severity=Severity.REPORT,
    subject="6765/05",
    message=(
        "Avtal 6765/05 (IBM Svenska AB, Programvaror och tjänster) har inget inläst "
        "huvuddokument: inget leverantörskort med avtalets nummer och inget huvuddokument på "
        "en sida för avtalets delområde."
    ),
    agreement_number="6765/05",
)
PARTY = Finding(
    check="supplier_party",
    severity=Severity.QUARANTINE,
    subject="556866-4444",
    message=(
        "Leverantörskortet för avtal 23.3-2940-20:033 har ÅF Digital Solutions AB, "
        "organisationsnummer 556866-4444, som avtalspart, men registret har AFRY Sweden AB, "
        "556224-8012, för avtalet."
    ),
    sha256=CARD,
    accepted_reason="ÅF Digital Solutions AB gick upp i AFRY Sweden AB under avtalstiden.",
)
FINDINGS = [PROCUREMENT, CITATION, ORG, PARTY, SCANNED, PAGE_PERIOD, UNCOVERED]

STALE = AcceptedFinding.model_validate(
    {
        "key": f"org_numbers:quarantine:{sha('8d679cb2ebef')}:556866-4444",
        "reason": "Numret står i en lista över tidigare leverantörer.",
        "reviewer": "Signe Granskare",
        "date": date(2026, 10, 7),
    }
)


def agreement(
    number: str,
    area: str,
    supplier: str,
    status: CoverageStatus,
    cards: tuple[str, ...] = (),
    main_documents: tuple[str, ...] = (),
    not_counted: tuple[NotCountedFile, ...] = (),
    procurement: str | None = None,  # by default the number without its sequence
) -> AgreementCoverage:
    return AgreementCoverage(
        agreement_number=number,
        procurement_number=procurement or (number.rsplit("-", 1)[0] if "-" in number else number),
        framework_area=area,
        supplier_name=supplier,
        status=status,
        cards=cards,
        main_documents=main_documents,
        not_counted=not_counted,
    )


def not_counted(sha256: str, reason: NotCounted, own: bool = False) -> NotCountedFile:
    return NotCountedFile(sha256, reason, own)


IT_KONSULT, BEMANNING, PROGRAMVAROR = AREAS[2], AREAS[1], AREAS[3]
TENDSIGN = not_counted(PRINTOUT, NotCounted.TENDSIGN_PRINTOUT)
COVERAGE = [
    # As in the pilot: its own card in quarantine, the area's main document a printout.
    # First in the register, but its area comes after Bemanningstjänster in the run.
    AgreementCoverage(
        agreement_number="23.3-2940-20:018",
        procurement_number="23.3-2940-20",
        framework_area=IT_KONSULT,
        supplier_name="AFRY Sweden AB",
        status=CoverageStatus.PROCUREMENT_VERSION,
        cards=(),
        main_documents=(),
        not_counted=(
            not_counted(CARD_018, NotCounted.QUARANTINED, own=True),
            not_counted(PRINTOUT_ITK2020, NotCounted.TENDSIGN_PRINTOUT),
        ),
    ),
    agreement(
        "23.3-14537-2023-001",
        BEMANNING,
        "A Hub Group AB",
        CoverageStatus.PROCUREMENT_VERSION,
        not_counted=(TENDSIGN,),
    ),
    agreement(
        "23.3-14537-2023-002",
        BEMANNING,
        "Academic Work Sweden AB",
        CoverageStatus.PROCUREMENT_VERSION,
        not_counted=(TENDSIGN,),
    ),
    agreement(
        "23.3-2940-20:033", IT_KONSULT, "AFRY Sweden AB", CoverageStatus.COVERED, cards=(CARD,)
    ),
    agreement(
        "23.3-1688-2024-001",
        IT_KONSULT,
        "Castra Group AB",
        CoverageStatus.COVERED,
        main_documents=(MAIN,),
    ),
    agreement(
        "23.3-1688-2024-002",
        IT_KONSULT,
        "Knowit AB",
        CoverageStatus.COVERED,
        cards=(CARD,),
        main_documents=(MAIN,),
        not_counted=(not_counted(DRAFT, NotCounted.TEMPLATE),),
    ),
    agreement(
        "23.5-3718-2024",
        PROGRAMVAROR,
        "Microsoft AB",
        CoverageStatus.HELD_BACK,
        not_counted=(not_counted(MICROSOFT, NotCounted.QUARANTINED),),
        procurement="23.5-3718-2024",  # the register has the agreement as its procurement
    ),
    agreement("6765/05", PROGRAMVAROR, "IBM Svenska AB", CoverageStatus.NOT_COVERED),
]


def link(sha256: str, title: str, page_title: str, page_url: str) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/{sha256[:12]}.pdf",
        title=title,
        category="Avtal",
        agreement_number=None,
        site_updated=None,
        page_url=page_url,
        page_title=page_title,
        page_procurement_numbers=(),
        page_period=None,
    )


IT_PAGE = PAGE.replace("kontorstjanster", "it-tjanster")
ITK2_PAGE = "https://www.avropa.se/ramavtal/it-konsulttjanster-2.-ledning-av-it-projekt/"
LINKS = [
    link(PRINTOUT, "Ramavtalets huvuddokument", PAGE_TITLE, PAGE),
    link(CARD_018, "Ramavtal", "IT-konsulttjänster 2. Ledning av IT-projekt", ITK2_PAGE),
    link(
        PRINTOUT_ITK2020,
        "Ramavtalets huvuddokument",
        "IT-konsulttjänster 2. Ledning av IT-projekt",
        ITK2_PAGE,
    ),
    link(
        PRINTOUT,
        "Ramavtalets huvuddokument",
        "Bemanningstjänster - IT-tjänster upp till 1000 timmar",
        IT_PAGE,
    ),
    link(
        DRAFT,
        "Ramavtalets huvuddokument",
        "IT-konsulttjänster 3. IT-säkerhet",
        "https://www.avropa.se/ramavtal/it-konsulttjanster-3.-it-sakerhet/",
    ),
    link(
        MICROSOFT,
        "Volymavtalets huvudavtal 1.0",
        "Volymavtal för Microsoft",
        "https://www.avropa.se/ramavtal/volymavtal-for-microsoft/",
    ),
]

NUMBER_STATUS = {
    MAIN: NumberStatus.MATCHES,
    CARD: NumberStatus.MATCHES,
    TERMS: NumberStatus.NO_NUMBER,
    TEMPLATE: NumberStatus.NO_NUMBER,
    DRAFT: NumberStatus.MATCHES,
    NO_TEXT: NumberStatus.NO_NUMBER,
    NUTS: NumberStatus.CASE_MANAGEMENT_ONLY,
    PRICE: NumberStatus.DEVIATES,
    CALL: NumberStatus.MATCHES,  # its own number matches; it only cites another (CITATION)
    PRINTOUT: NumberStatus.MATCHES,
    MICROSOFT: NumberStatus.CASE_MANAGEMENT_ONLY,
}


# Files the coverage lines name but the hand-made run does not count: their metadata only.
NAMED_ONLY = [
    metadata(
        CARD_018,
        "Ramavtal",
        DocumentType.SUPPLIER_AGREEMENT,
        "R01",
        agreement_number="23.3-2940-20:018",
    ),
    metadata(
        PRINTOUT_ITK2020,
        "Ramavtalets huvuddokument",
        DocumentType.MAIN_DOCUMENT,
        "R05",
        tendsign_cover="Upphandlingsdokument",
    ),
]


STATS = MatchStats(asked=3, answered=2, calls=1, cache_hits=2)


def run_result(
    findings: list[Finding] = FINDINGS,
    references: list[Reference] = REFERENCES,
    stats: MatchStats | None = STATS,
    quarantine: Quarantine | None = None,
    links: list[CatalogLink] = LINKS,
) -> IngestionResult:
    parsed, chunked, extractions = [], [], []
    for meta, file_type, (ocr, page_count), outline, sections, chunk_count, missing in FILES:
        pages = tuple(
            PageInfo(number=n, char_count=0 if n in ocr else 900, needs_ocr=n in ocr)
            for n in range(1, page_count + 1)
        )
        parsed.append(
            ParsedDocument(
                sha256=meta.sha256, file_type=file_type, parser="test", pages=pages, blocks=()
            )
        )
        chunked.append(
            ChunkedDocument(meta.sha256, outline, sections, chunks(chunk_count), missing)
        )
        extractions.append(DocumentExtraction(metadata=meta, facts=(), mentions=()))
    extractions += [DocumentExtraction(metadata=m, facts=(), mentions=()) for m in NAMED_ONLY]
    held = quarantine or Quarantine(
        files=frozenset({PRICE, MICROSOFT}), sections=frozenset({(TERMS, 1)})
    )
    validation = Validation(
        findings=findings,
        quarantine=held,
        coverage=COVERAGE,
        number_status=NUMBER_STATUS,
        unused_acceptances=[STALE],
    )
    corpus = CorpusExtraction(extractions, references, rules_rate=7 / 13, match_stats=stats)
    return IngestionResult(parsed, chunked, [UNLINKED], corpus, validation, list(links))


RUN = RunInfo(
    started_at=START,
    finished_at=START + timedelta(minutes=4, seconds=12),
    register_version=RegisterVersion(
        list_date=date(2026, 10, 5), title="Giltiga ramavtal 2026-10-05"
    ),
    areas=AREAS,
    model="gpt-6-luna",
)


@pytest.fixture(scope="module")
def report() -> IngestionReport:
    return build_report(run_result(), RUN)


@pytest.fixture(scope="module")
def markdown(report: IngestionReport) -> str:
    return render_markdown(report)


def part(markdown: str, heading: str) -> str:
    """The text of one "## " section of the markdown."""
    start = markdown.index(f"## {heading}\n")
    end = markdown.find("\n## ", start + 1)
    return markdown[start : end if end != -1 else None].rstrip()


# --- Körning -------------------------------------------------------------------------------


def test_the_run_names_its_times_register_areas_and_language_model(markdown: str) -> None:
    run = part(markdown, "Körning")
    assert "2026-10-06 14:03:12 (UTC+02:00)" in run
    assert "efter 4 min 12 s" in run
    assert "**Registret:** Giltiga ramavtal 2026-10-05\n" in run
    assert "IT-drift, Bemanningstjänster, IT-konsulttjänster Resurskonsulter" in run
    assert "gpt-6-luna" in run
    assert "3 frågor, 2 besvarade" in run
    assert "1 anrop till modellen och 2 svar från cachen" in run


def test_a_run_without_the_language_model_says_so() -> None:
    run = RunInfo(START, START, None, (), None)
    markdown = render_markdown(build_report(run_result(stats=None), run))
    assert "**Språkmodell:** kördes inte" in markdown
    assert "Språkmodellen kördes inte, så andelen är reglernas: 53,8" in markdown
    assert "**Registret:** okänd" in markdown


def test_run_info_needs_timezone_aware_times() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RunInfo(datetime(2026, 10, 6, 14, 3), START, None, AREAS, None)


def test_the_duration_agrees_with_the_clock_times_it_follows() -> None:
    # The pilot run took 19.53 s, from 17:02:23.456 to 17:02:42.987; the times are printed
    # to the second, so the duration is the 19 s between them, not 20.
    run = RunInfo(
        started_at=datetime(2026, 10, 6, 17, 2, 23, 456262, tzinfo=UTC),
        finished_at=datetime(2026, 10, 6, 17, 2, 42, 986656, tzinfo=UTC),
        register_version=None,
        areas=AREAS,
        model=None,
    )
    text = part(render_markdown(build_report(run_result(), run)), "Körning")
    assert "- **Start:** 2026-10-06 17:02:23 (UTC)\n" in text
    assert "- **Slut:** 2026-10-06 17:02:42 (UTC), efter 19 s\n" in text

    # 0.2 s that cross a whole second (made up): the clock times say 1 s, not 0.
    short = replace(
        run,
        started_at=datetime(2026, 10, 6, 17, 2, 23, 900000, tzinfo=UTC),
        finished_at=datetime(2026, 10, 6, 17, 2, 24, 100000, tzinfo=UTC),
    )
    text = part(render_markdown(build_report(run_result(), short)), "Körning")
    assert "- **Slut:** 2026-10-06 17:02:24 (UTC), efter 1 s\n" in text


# --- Dokument ------------------------------------------------------------------------------


def test_documents_are_counted_by_type_and_type_rule(report: IngestionReport) -> None:
    documents = report.documents
    assert documents.files == 11
    assert documents.by_file_type == {"pdf": 10, "docx": 1}
    by_type = {item.document_type: item for item in documents.by_type}
    assert by_type[DocumentType.MAIN_DOCUMENT].files == 4
    assert by_type[DocumentType.MAIN_DOCUMENT].rules == {"R05": 4}
    assert by_type[DocumentType.ANNEX].rules == {"F1": 1}
    # In the order of DocumentType, types without files left out.
    assert [item.document_type for item in documents.by_type][:2] == [
        DocumentType.SUPPLIER_AGREEMENT,
        DocumentType.MAIN_DOCUMENT,
    ]
    assert DocumentType.UNKNOWN not in by_type


def test_document_types_have_their_swedish_names_and_fallback_rules_are_marked(
    markdown: str,
) -> None:
    documents = part(markdown, "Dokument")
    assert "| Huvuddokument | avtalet | 4 | R05 4 |" in documents
    assert "| Bilaga | avtalet | 1 | F1 1 (reservregel) |" in documents
    assert "| Mall | stöd för avrop | 1 | R10 1 |" in documents
    assert "(PDF 10, Word 1)" in documents


def test_fallback_rules_come_after_the_rules_that_read_the_link() -> None:
    result = run_result()
    annex = sha("3f646362f5b7")  # link "Bilaga 1 Kontaktuppgifter", rule R14
    result.parsed.append(
        ParsedDocument(sha256=annex, file_type="pdf", parser="test", pages=(), blocks=())
    )
    result.chunked.append(ChunkedDocument(annex, OutlineKind.NONE, [], [], None))
    meta = metadata(annex, "Bilaga 1 Kontaktuppgifter", DocumentType.ANNEX, "R14")
    result.corpus.extractions.append(DocumentExtraction(metadata=meta, facts=(), mentions=()))
    report = build_report(result, RUN)
    [annexes] = [t for t in report.documents.by_type if t.document_type is DocumentType.ANNEX]
    assert list(annexes.rules) == ["R14", "F1"]


def test_templates_by_type_are_counted_and_the_others_listed_with_their_type(
    report: IngestionReport, markdown: str
) -> None:
    assert [ref.sha256 for ref in report.documents.templates] == [TEMPLATE, DRAFT, PRINTOUT]
    documents = part(markdown, "Dokument")
    assert "**Mallar och utkast:** 3 filer." in documents
    assert "1 har typen Mall och 2 har ett tomt datumfält:" in documents
    assert "Avropsmall (`232f65cf161a`)" not in documents  # the link says it is a template


def test_a_template_is_listed_with_the_pages_that_link_to_it(markdown: str) -> None:
    # Five pilot templates are called "Ramavtalets huvuddokument"; the page tells them apart.
    documents = part(markdown, "Dokument")
    assert (
        "- Ramavtalets huvuddokument (`65d611d12eab`), huvuddokument, på sidan "
        "IT-konsulttjänster 3. IT-säkerhet\n" in documents
    )
    assert (
        "- Ramavtalets huvuddokument (`34d71a7e4da0`), huvuddokument, på sidorna "
        f"{PAGE_TITLE} och Bemanningstjänster - IT-tjänster upp till 1000 timmar\n" in documents
    )


def test_files_with_scanned_pages_list_the_pages_in_runs(
    report: IngestionReport, markdown: str
) -> None:
    assert [item.file.sha256 for item in report.documents.ocr_files] == [TERMS, NO_TEXT]
    documents = part(markdown, "Dokument")
    assert "| Allmänna villkor (`e04bad6a0ced`) | 1, 6-13 | 31 |" in documents
    assert "| Volymavtal (`21dd4fde89d5`) | 1-10 | 10 |" in documents


def test_scanned_pages_are_written_as_the_missing_text_check_writes_them(
    report: IngestionReport,
) -> None:
    # e04bad6a0ced in the pilot. Its missing_text note has the subject "s. 1, 6-13, 15-21,
    # 24-25, 27-30"; the Dokument table gives the same pages the same way.
    pages = (1, *range(6, 14), *range(15, 22), 24, 25, *range(27, 31))
    terms = report.documents.ocr_files[0].model_copy(update={"pages": pages})
    documents = report.documents.model_copy(update={"ocr_files": (terms,)})
    markdown = render_markdown(report.model_copy(update={"documents": documents}))
    assert "| Allmänna villkor (`e04bad6a0ced`) | 1, 6-13, 15-21, 24-25, 27-30 | 31 |" in markdown


def test_files_no_page_links_to_any_more_are_listed(report: IngestionReport, markdown: str) -> None:
    assert report.documents.unlinked == (UNLINKED,)
    assert "`5c9b05f2cc79`. De lästes inte in och tas bort ur indexet." in markdown


# --- Avsnitt och chunkar -------------------------------------------------------------------


def test_sections_chunks_and_outlines_are_counted(report: IngestionReport) -> None:
    sections = report.sections
    assert (sections.sections, sections.chunks) == (12, 13)
    assert sections.by_outline == {
        OutlineKind.NUMBERED: 7,
        OutlineKind.QUESTIONS: 0,
        OutlineKind.HEADINGS: 1,
        OutlineKind.NONE: 3,
    }
    assert [ref.sha256 for ref in sections.without_sections] == [NO_TEXT]


def test_a_section_is_not_said_to_be_numbered(markdown: str) -> None:
    # 43 of the pilot's 207 files have headings without numbers, questions or no headings.
    text = part(markdown, "Avsnitt och chunkar")
    assert (
        "Ett avsnitt är texten under en rubrik (”14.2 Leverantörens uppsägning”) eller före den "
        "första, en fråga i en frågelogg, eller hela filen när den saknar rubriker;" in text
    )
    assert "numrerat avsnitt" not in text


def test_a_table_of_contents_with_numbers_that_are_no_section_is_listed(
    report: IngestionReport, markdown: str
) -> None:
    assert report.sections.with_contents == 2
    assert [(gap.file.sha256, gap.missing) for gap in report.sections.contents_gaps] == [
        (TERMS, ("7.17", "7.18"))
    ]
    text = part(markdown, "Avsnitt och chunkar")
    assert "I 1 av dem är varje nummer i förteckningen ett avsnitt; i de övriga saknas:" in text
    assert "- Allmänna villkor (`e04bad6a0ced`): 7.17, 7.18" in text
    assert "| Inga rubriker, hela filen är ett avsnitt | 3 |" in text


# --- Stämmer med registret -----------------------------------------------------------------


def test_each_files_numbers_are_counted_and_deviating_files_named(
    report: IngestionReport, markdown: str
) -> None:
    match = report.register_match
    assert match.by_status == {
        NumberStatus.MATCHES: 5,
        NumberStatus.NO_NUMBER: 3,
        NumberStatus.CASE_MANAGEMENT_ONLY: 2,
        NumberStatus.DEVIATES: 1,
    }
    assert [(item.file.sha256, item.numbers) for item in match.deviating] == [
        (PRICE, {"23.3-1688-2024": Severity.QUARANTINE}),
    ]
    text = part(markdown, "Stämmer med registret")
    assert "Varje fils egna diarie- och avtalsnummer jämförs" in text
    # 54211e718d8e cites 23.3-7067-17 in parentheses: a note, not a file that deviates.
    assert (
        "Ett nummer som dokumentet citerar inom parentes ändrar inte utfallet; hör det till en "
        "annan upphandling står det som en notering under Fynd." in text
    )
    assert "| Avviker | 1 |" in text
    assert (
        "- Prisbilaga - sammanställning Delområde 3 (`185c8246e536`): 23.3-1688-2024 (karantän)"
        in text
    )


def test_a_deviating_file_lists_only_the_numbers_of_the_procurement_number_check() -> None:
    other = ORG.model_copy(update={"sha256": PRICE})  # another check's finding on the file
    report = build_report(run_result(findings=[PROCUREMENT, other]), RUN)
    [price] = report.register_match.deviating
    assert price.numbers == {"23.3-1688-2024": Severity.QUARANTINE}


def test_an_accepted_number_is_labelled_accepted_not_by_its_severity() -> None:
    # Accepting 185c8246e536's number leaves the finding's severity as it was; the file is
    # released (not in the quarantine below), so this section must not say "karantän".
    accepted = PROCUREMENT.model_copy(update={"accepted_reason": "Rubriken är fel, inte filen."})
    held = Quarantine(files=frozenset({MICROSOFT}), sections=frozenset({(TERMS, 1)}))
    report = build_report(run_result(findings=[accepted, ORG, SCANNED], quarantine=held), RUN)

    [price] = report.register_match.deviating
    assert price.numbers == {"23.3-1688-2024": "accepted"}
    markdown = render_markdown(report)
    text = part(markdown, "Stämmer med registret")
    assert (
        "- Prisbilaga - sammanställning Delområde 3 (`185c8246e536`): 23.3-1688-2024 (godkänd)"
        in text
    )
    assert "(karantän)" not in text
    assert "185c8246e536" not in part(markdown, "Karantän")
    data = render_json(report)
    assert json.loads(data)["register_match"]["deviating"][0]["numbers"] == {
        "23.3-1688-2024": "accepted"
    }
    assert IngestionReport.model_validate_json(data) == report


# --- Täckning ------------------------------------------------------------------------------


def test_coverage_is_counted_per_area_and_by_how_it_is_covered(report: IngestionReport) -> None:
    areas = {area.framework_area: area for area in report.coverage.areas}
    assert list(areas) == list(AREAS)  # the run's areas, also one without agreements
    assert areas["IT-drift"].agreements == 0
    konsult = areas[IT_KONSULT]
    assert (konsult.covered, konsult.by_card, konsult.by_main_document, konsult.by_both) == (
        3,
        1,
        1,
        1,
    )
    assert konsult.procurement_version == 1
    assert areas[BEMANNING].procurement_version == 2
    programvaror = areas[PROGRAMVAROR]
    assert (programvaror.procurement_version, programvaror.held_back) == (0, 1)
    assert programvaror.not_covered == 1


def test_agreements_not_covered_come_in_the_coverage_checks_groups(
    report: IngestionReport,
) -> None:
    groups = report.coverage.groups
    # By status, then in the order of the run's areas. A group's subject is its
    # procurement, also for one agreement, unless that agreement is not covered.
    assert [(group.status, group.subject) for group in groups] == [
        (CoverageStatus.PROCUREMENT_VERSION, "23.3-14537-2023"),
        (CoverageStatus.PROCUREMENT_VERSION, "23.3-2940-20"),
        (CoverageStatus.HELD_BACK, "23.5-3718-2024"),
        (CoverageStatus.NOT_COVERED, "6765/05"),
    ]
    bemanning, afry, microsoft, ibm = groups
    assert [gap.agreement_number for gap in bemanning.agreements] == [
        "23.3-14537-2023-001",
        "23.3-14537-2023-002",
    ]
    assert [(item.file.sha256, item.reason) for item in bemanning.files] == [
        (PRINTOUT, NotCounted.TENDSIGN_PRINTOUT)
    ]
    # The file the group is formed by first, then its own card in quarantine.
    assert [(item.file.sha256, item.reason) for item in afry.files] == [
        (PRINTOUT_ITK2020, NotCounted.TENDSIGN_PRINTOUT),
        (CARD_018, NotCounted.QUARANTINED),
    ]
    assert [(item.file.sha256, item.reason) for item in microsoft.files] == [
        (MICROSOFT, NotCounted.QUARANTINED)
    ]
    assert (ibm.framework_areas, ibm.files) == ((PROGRAMVAROR,), ())


def test_coverage_has_a_column_and_a_line_per_group_for_each_status(markdown: str) -> None:
    text = part(markdown, "Täckning")
    assert (
        "| Ramavtalsområde | Avtal | Täckta | via leverantörsavtal | via huvuddokument "
        "| via båda | Bara upphandlingens version | Bara dokument i karantän | Inte täckta |"
    ) in text
    assert "| **Totalt** | **8** | **3** | **1** | **1** | **1** | **3** | **1** | **1** |" in text
    assert (
        "- **Bara upphandlingens version: Bemanningstjänster, 2 avtal** (23.3-14537-2023): "
        "Ramavtalets huvuddokument (`34d71a7e4da0`), upphandlingens version från TendSign, på "
        f"sidorna {PAGE_TITLE} och Bemanningstjänster - IT-tjänster upp till 1000 timmar." in text
    )
    # A supplier's own agreement is named by its number, not its page.
    assert (
        "- **Bara upphandlingens version: IT-konsulttjänster Resurskonsulter, 1 avtal** "
        "(23.3-2940-20:018, AFRY Sweden AB): Ramavtalets huvuddokument (`cbe12fd30683`), "
        "upphandlingens version från TendSign, på sidan IT-konsulttjänster 2. Ledning av "
        "IT-projekt; Ramavtal 23.3-2940-20:018 (`ee6107229c37`), i karantän.\n" in text
    )
    assert (
        "- **Bara dokument i karantän: Programvaror och tjänster, 1 avtal** (23.5-3718-2024, "
        "Microsoft AB): Volymavtalets huvudavtal 1.0 (`171a3cacf5fd`), i karantän, på sidan "
        "Volymavtal för Microsoft." in text
    )
    assert (
        "- **Inte täckt: Programvaror och tjänster, 1 avtal** (6765/05, IBM Svenska AB): inget "
        "inläst huvuddokument." in text
    )


def test_coverage_says_why_the_procurements_version_is_what_is_indexed(markdown: str) -> None:
    text = part(markdown, "Täckning")
    assert (
        "den undertecknade versionen av de avtalen publiceras inte på avropa.se, så det är "
        "upphandlingens version som indexeras." in text
    )
    summary = part(markdown, "Sammanfattning")
    assert (
        "- Täckning: 3 av 8 avtal är täckta; 3 har bara upphandlingens version av "
        "huvuddokumentet (den undertecknade publiceras inte på avropa.se), 1 bara dokument i "
        "karantän och 1 är inte täckt." in summary
    )


# --- Hänvisningar --------------------------------------------------------------------------


def test_the_rate_leaves_out_laws_questions_list_items_self_and_external(
    report: IngestionReport,
) -> None:
    refs = report.references
    assert refs.references == 20
    assert (refs.laws, refs.questions, refs.list_items, refs.self_references) == (2, 2, 1, 2)
    assert refs.external == 0
    left_out = refs.laws + refs.questions + refs.list_items + refs.self_references
    assert refs.counted == refs.references - left_out - refs.external
    assert refs.counted == sum(1 for r in REFERENCES if counts_in_rate(r)) == 13
    assert refs.resolved == 8
    assert refs.rate == resolution_rate(REFERENCES)
    assert refs.rules_rate == 7 / 13
    assert (refs.model_titles, refs.model_topics) == (1, 1)
    assert refs.by_status[ReferenceStatus.NOT_PUBLISHED] == 2
    assert refs.counted_by_status[ReferenceStatus.NUMBER_MISSING] == 0  # a "fråga N"


def test_the_formula_is_spelled_out_with_swedish_numbers(markdown: str) -> None:
    text = part(markdown, "Hänvisningar")
    formula = f"= 8 / (20 − 2 − 2 − 1 − 2 − 0) = 8 / 13 = **61,5{NBSP}%**"
    assert formula in text
    assert f"**Bara med regler:** 53,8{NBSP}%" in text
    assert "valde rubriken för 1 hänvisning där reglerna bara hittade filen (R4-llm)" in text
    assert "**Självhänvisningar:** 2, räknas inte." in text
    assert "**Ej publicerade:** 2, räknas som ej upplösta" in text


def test_references_are_counted_per_rule_with_the_text_decided_ones_last(
    report: IngestionReport, markdown: str
) -> None:
    rules = [tally.rule for tally in report.references.by_rule]
    assert rules == ["R1", "R1q", "R2", "R2-llm", "R4", "R4-llm", "R5", "RQ", None]
    r2 = next(tally for tally in report.references.by_rule if tally.rule == "R2")
    assert (r2.references, r2.counted, r2.resolved) == (3, 2, 2)
    assert r2.statuses == {ReferenceStatus.RESOLVED: 2, ReferenceStatus.SELF: 1}
    text = part(markdown, "Hänvisningar")
    assert "| R5 | bilaga med namn, bland filerna på avtalssidan | 2 | 2 | 0 | 0,0" in text
    assert "| ej publicerad på avtalssidan 2 |" in text
    assert "| RQ | ”fråga N” i samma frågelogg | 2 | 0 | 0 | – |" in text
    assert "| – | texten avgör (lag, mallfält, listpunkt, självhänvisning) | 5 |" in text


def test_references_are_counted_per_kind(report: IngestionReport) -> None:
    kinds = {tally.kind: tally for tally in report.references.by_kind}
    assert kinds[ReferenceKind.SECTION_TITLE].references == 5
    assert kinds[ReferenceKind.SECTION_TITLE].resolved == 4
    assert kinds[ReferenceKind.LAW].counted == 0
    assert ReferenceKind.ANNEX_NUMBER not in kinds


# --- Fynd ----------------------------------------------------------------------------------


def test_findings_of_every_severity_are_called_fynd(markdown: str) -> None:
    # A note is no deviation (ADR 0009 decision 6), so the count and the headings say "fynd".
    summary = part(markdown, "Sammanfattning")
    assert "- 6 fynd: karantän 3, rapport 2, notering 1. 1 godkänt." in summary
    assert "avvikelse" not in summary
    assert "**Notering:** värt att veta, ingen avvikelse." in part(markdown, "Fynd")
    assert "står under Fynd, Täckning." in part(markdown, "Täckning")
    assert "## Godkända fynd\n" in markdown
    assert "## Avvikelser" not in markdown


def test_findings_are_counted_per_check_and_severity(report: IngestionReport) -> None:
    by_check = {count.check: count for count in report.findings.by_check}
    # Every check of step 5, in the order they run, also those that found nothing.
    assert list(by_check) == [check.CHECK for check in DOCUMENT_CHECKS] + ["coverage"]
    assert by_check["procurement_number"].by_severity == {
        Severity.QUARANTINE: 1,
        Severity.REPORT: 0,
        Severity.NOTE: 1,
    }
    assert by_check["supplier_party"].by_severity[Severity.QUARANTINE] == 0
    assert by_check["supplier_party"].accepted == 1


def test_open_findings_come_quarantine_first_and_leave_out_the_accepted(
    report: IngestionReport,
) -> None:
    open_findings = [item.finding for item in report.findings.open]
    assert open_findings == [PROCUREMENT, ORG, SCANNED, PAGE_PERIOD, UNCOVERED, CITATION]
    assert PARTY not in open_findings
    keys = [item.key for item in report.findings.open]
    assert keys[0] == PROCUREMENT.key


def test_each_finding_is_printed_under_its_severity_and_check(markdown: str) -> None:
    text = part(markdown, "Fynd")
    assert "| Diarienummer | " in text
    assert "| 1 | 0 | 1 | 0 |" in text  # Diarienummer: Q, R, N, accepted
    quarantine = text.index("### Karantän (3)")
    report = text.index("### Rapport (2)")
    assert quarantine < text.index("#### Diarienummer (1)") < report
    assert (
        "- Prisbilaga - sammanställning Delområde 3 (`185c8246e536`): 23.3-1688-2024\n"
        "  - Diarienumret 23.3-1688-2024 (s. 1) tillhör inte upphandlingen" in text
    )
    assert "  - Underlag: ”23.3-1688-2024 IT-konsulttjänster - IT-säkerhet”" in text
    assert f"  - Nyckel: `{PROCUREMENT.key}`" in text
    assert "- Allmänna villkor (`e04bad6a0ced`), avsnitt 7.16 Prismodeller\n" in text


def test_a_finding_about_a_page_or_an_agreement_is_named_by_it(markdown: str) -> None:
    text = part(markdown, "Fynd")
    assert f"- Sidan [{PAGE_TITLE}]({PAGE}): 2025-04-22 - 2029-04-21" in text
    assert "- Avtal 6765/05\n" in text  # the subject is the agreement; not repeated


def test_a_page_no_link_of_the_run_is_on_is_named_by_its_address() -> None:
    markdown = render_markdown(build_report(run_result(links=[]), RUN))
    assert (
        "- Sidan [bemanningstjanster---kontorstjanster-upp-till-1000-timmar]"
        f"({PAGE}): 2025-04-22 - 2029-04-21" in part(markdown, "Fynd")
    )


def test_a_coverage_finding_about_a_group_is_named_by_its_procurement() -> None:
    group = Finding(
        check="coverage",
        severity=Severity.NOTE,
        subject="23.3-14537-2023",
        message=(
            "33 avtal inom Bemanningstjänster täcks bara av huvuddokument som indexeras men inte "
            "är det undertecknade avtalet: 34d71a7e4da0 (upphandlingens version från TendSign)."
        ),
    )
    markdown = render_markdown(build_report(run_result(findings=[group]), RUN))
    assert "- Avtal i 23.3-14537-2023\n  - 33 avtal inom" in part(markdown, "Fynd")


# --- Karantän ------------------------------------------------------------------------------


def test_quarantine_lists_files_and_sections_with_the_findings_that_hold_them(
    report: IngestionReport,
) -> None:
    held = report.quarantine
    assert [(item.file.sha256, [f.finding for f in item.findings]) for item in held.files] == [
        (PRICE, [PROCUREMENT]),
        (MICROSOFT, [ORG]),
    ]
    [scanned] = held.sections
    assert (scanned.file.sha256, scanned.section, scanned.heading) == (
        TERMS,
        1,
        "7.16 Prismodeller",
    )
    assert [f.key for f in scanned.findings] == [SCANNED.key]


def test_quarantine_says_how_to_accept_a_finding(markdown: str) -> None:
    text = part(markdown, "Karantän")
    assert "skrivs dess nyckel in i accepted_findings.toml i repots rot, med skäl" in text
    assert "[[accepted]]" in text
    assert "date = 2026-10-06" in text
    assert "- **Volymavtalets huvudavtal 1.0 (`171a3cacf5fd`)**" in text
    assert f"Nyckel: `{ORG.key}`" in text
    assert "- **Allmänna villkor (`e04bad6a0ced`)**, avsnitt 7.16 Prismodeller" in text


def test_the_report_names_the_accepted_findings_file_of_the_settings() -> None:
    # ACCEPTED_FINDINGS_FILE can point elsewhere; the report must send a person there.
    run = replace(RUN, accepted_file=Path("/srv/avtalsagent/godkanda.toml"))
    markdown = render_markdown(build_report(run_result(), run))
    assert "skrivs dess nyckel in i /srv/avtalsagent/godkanda.toml, med skäl" in markdown
    assert "har godkänt i /srv/avtalsagent/godkanda.toml, med skälet" in markdown
    assert "Godkännanden i /srv/avtalsagent/godkanda.toml som inte motsvarar" in markdown
    assert "accepted_findings.toml" not in markdown


def test_unchecked_files_are_listed_apart_from_those_a_finding_holds() -> None:
    # PRICE has a finding from an earlier run, but steps 4-5 have not run on it since.
    held = Quarantine(
        files=frozenset({PRICE, NUTS, MICROSOFT}),
        sections=frozenset(),
        unchecked=frozenset({PRICE, NUTS}),
    )
    report = build_report(run_result(quarantine=held), RUN)
    assert [item.file.sha256 for item in report.quarantine.files] == [MICROSOFT]
    assert [ref.sha256 for ref in report.quarantine.unchecked] == [PRICE, NUTS]
    assert "### Ej kontrollerade (2)\n" in render_markdown(report)


def test_nothing_held_back_is_said_plainly() -> None:
    empty = Quarantine(files=frozenset(), sections=frozenset())
    report = build_report(run_result(findings=[UNCOVERED], quarantine=empty), RUN)
    assert part(render_markdown(report), "Karantän").endswith("Inget hålls tillbaka.")


# --- Godkända fynd -------------------------------------------------------------------------


def test_accepted_findings_are_listed_with_their_reason(
    report: IngestionReport, markdown: str
) -> None:
    assert [item.finding for item in report.accepted.findings] == [PARTY]
    text = part(markdown, "Godkända fynd")
    assert (
        "- Ramavtal 23.3-2940-20:033 (`7a49e1a61b31`) – Leverantör i avtalet: 556866-4444" in text
    )
    assert "  - Skäl: ÅF Digital Solutions AB gick upp i AFRY Sweden AB under avtalstiden." in text


def test_acceptances_that_match_nothing_are_listed_without_the_reviewer(
    report: IngestionReport, markdown: str
) -> None:
    [unused] = report.accepted.unused
    assert (unused.key, unused.accepted_on) == (STALE.key, date(2026, 10, 7))
    assert f"- `{STALE.key}`, godkänd 2026-10-07: Numret står i en lista" in markdown
    assert "Signe Granskare" not in markdown
    assert "Signe Granskare" not in render_json(report)


# --- Formatting, JSON and files ------------------------------------------------------------


def test_a_count_of_one_takes_the_singular(report: IngestionReport) -> None:
    # A run of one of everything: every count phrase of the report in the singular.
    documents = report.documents.model_copy(
        update={
            "files": 1,
            "pdf_pages": 1,
            "templates": report.documents.templates[:1],
            "ocr_files": report.documents.ocr_files[:1],
        }
    )
    sections = report.sections.model_copy(
        update={
            "chunks": 1,
            "with_contents": 1,
            "by_outline": {kind: int(kind is OutlineKind.NUMBERED) for kind in OutlineKind},
        }
    )
    by_status = {**report.references.by_status, ReferenceStatus.NOT_PUBLISHED: 1}
    references = report.references.model_copy(
        update={"references": 1, "model_titles": 1, "model_topics": 1, "by_status": by_status}
    )
    run = report.run.model_copy(
        update={"matching": MatchStats(asked=1, answered=1, calls=1, cache_hits=0)}
    )
    findings = report.findings.model_copy(update={"open": report.findings.open[:1]})
    quarantine = report.quarantine.model_copy(
        update={"files": (), "unchecked": (report.quarantine.files[0].file,)}
    )
    [konsult] = [area for area in report.coverage.areas if area.framework_area == IT_KONSULT]
    one_each = konsult.model_copy(
        update={
            "agreements": 2,
            "covered": 1,
            "procurement_version": 0,
            "held_back": 0,
            "not_covered": 1,
        }
    )
    coverage = report.coverage.model_copy(update={"areas": (one_each,)})
    single = report.model_copy(
        update={
            "documents": documents,
            "sections": sections,
            "references": references,
            "run": run,
            "findings": findings,
            "quarantine": quarantine,
            "coverage": coverage,
        }
    )

    markdown = render_markdown(single)

    for phrase in (
        "- 1 fil lästes in och gav 12 avsnitt och 1 chunk.",
        "- 1 fynd: karantän 1, rapport 0, notering 0. 1 godkänt.",
        "- I karantän: 1 fil och 1 avsnitt.",
        "- Täckning: 1 av 2 avtal är täckt; 0 har bara upphandlingens version",
        "0 bara dokument i karantän och 1 är inte täckt.",
        "1 fråga, 1 besvarad med en av rubrikerna.",
        "1 fil lästes in (PDF 10, Word 1), med sammanlagt 1 PDF-sida.",
        "**Mallar och utkast:** 1 fil.",
        "**Filer med sidor utan textlager:** 1 fil.",
        "Steg 3 delade 1 fil i 12 avsnitt och 1 chunk.",
        "**Innehållsförteckning:** 1 fil har en. I den saknas:",
        "Steg 4 hittade 1 hänvisning i avsnittens text",
        "valde rubriken för 1 hänvisning där reglerna bara hittade filen",
        "och avsnittet för 1 hänvisning till ett dokument med ett ämne",
        "**Ej publicerade:** 1, räknas som ej upplöst:",
        "Steg 4 och 5 har inte körts på den här filen sedan steg 3 senast sparade den:",
    ):
        assert phrase in markdown, phrase


def test_numbers_are_written_the_swedish_way(report: IngestionReport) -> None:
    sections = report.sections.model_copy(update={"sections": 13175, "chunks": 13979})
    references = report.references.model_copy(update={"rate": 0.7444})
    markdown = render_markdown(
        report.model_copy(update={"sections": sections, "references": references})
    )
    assert f"13{NBSP}175 avsnitt och 13{NBSP}979 chunkar" in markdown
    assert f"74,4{NBSP}% av hänvisningarna är upplösta" in markdown


def test_markup_in_a_title_is_escaped() -> None:
    result = run_result()
    meta = result.corpus.extractions[7].metadata.model_copy(
        update={"title": "Prisbilaga | Delområde 3 *utkast*"}
    )
    result.corpus.extractions[7] = DocumentExtraction(metadata=meta, facts=(), mentions=())
    markdown = render_markdown(build_report(result, RUN))
    assert "Prisbilaga \\| Delområde 3 \\*utkast\\* (`185c8246e536`)" in markdown
    assert "Prisbilaga | Delområde 3" not in markdown


def test_every_enum_value_has_a_swedish_name() -> None:
    assert set(DOCUMENT_TYPE_NAMES) == set(DocumentType)
    assert set(OUTLINE_NAMES) == set(OutlineKind)
    assert set(NUMBER_STATUS_NAMES) == set(NumberStatus)
    assert set(REFERENCE_KIND_NAMES) == set(ReferenceKind)
    assert set(REFERENCE_STATUS_NAMES) == set(ReferenceStatus)
    assert set(SEVERITY_NAMES) == set(Severity)
    assert set(COVERAGE_STATUS_NAMES) == set(CoverageStatus)
    assert set(NOT_COUNTED_NAMES) == set(NotCounted)
    assert set(CHECK_NAMES) == {check.CHECK for check in DOCUMENT_CHECKS} | {"coverage"}


def test_the_json_holds_the_whole_report_and_reads_back(report: IngestionReport) -> None:
    text = render_json(report)
    assert IngestionReport.model_validate_json(text) == report
    data = json.loads(text)
    assert data["references"]["counted"] == 13
    assert data["documents"]["by_type"][0]["document_type"] == "supplier_agreement"
    assert data["findings"]["open"][0]["key"] == PROCUREMENT.key
    assert "sammanställning Delområde 3" in text  # written as UTF-8, not \\u escapes


def test_the_report_is_written_as_markdown_and_json_named_after_the_start(
    report: IngestionReport, tmp_path: Path
) -> None:
    folder = tmp_path / "data" / "reports"
    markdown, data = write_report(render_report(report), folder)
    assert markdown == folder / "2026-10-06T140312-inlasning.md"
    assert data == folder / "2026-10-06T140312-inlasning.json"
    assert markdown.read_text(encoding="utf-8") == render_markdown(report)
    assert IngestionReport.model_validate_json(data.read_text(encoding="utf-8")) == report


def test_the_start_in_utc_names_the_file_by_its_own_clock(tmp_path: Path) -> None:
    run = RunInfo(datetime(2026, 10, 6, 12, 3, 12, tzinfo=UTC), START, None, AREAS, None)
    markdown, _ = write_report(render_report(build_report(run_result(), run)), tmp_path)
    assert markdown.name == "2026-10-06T120312-inlasning.md"
