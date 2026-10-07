"""Tests for avtalsagent.ingestion.pipeline, from a parsed file to the ingestion report.

One main document on one agreement page goes through steps 3-5 and into the report.
It names a supplier whose organisation number is in no register row, so step 5 holds
it back, and it refers to a section of its own ("punkt 3.1"), so the reference rate
has something to count. The register is the sample register
(tests/fixtures/register_sample.tsv): its rows of 23.3-14537-2023-001 (A Hub Group AB,
559199-9601) are in the sub-area the page is for.

The lines: the party clause is 185872a6bb90 §1.3.1 (34d71a7e4da0 p4 has the same
clause unfilled), with a made-up supplier and organisation number; the reference is
written like 69efbee998c7 §3.1.2 "Miljö och hållbarhet, enligt punkt 3.1.1 ovan.".
The link and the page are 34d71a7e4da0's: "Ramavtalets huvuddokument" on
"Bemanningstjänster - IT-tjänster upp till 1000 timmar" (23.3-14537-2023).

The last test builds the document as a PDF with reportlab and parses it with Docling
(step 2), so the whole chain runs. It needs Docling's layout models and is skipped
without them unless AVTALSAGENT_REQUIRE_MODELS=1 (set in CI), as in
test_docling_parser.py. The other tests build the parsed file by hand, so the
pipeline is tested also where the models are missing.
"""

import hashlib
import os
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    FactKind,
    Finding,
    ReferenceKind,
    ReferenceStatus,
    Severity,
)
from avtalsagent.domain.parsed import Block, BlockKind, PageInfo, ParsedDocument
from avtalsagent.domain.register import RegisterEntry, RegisterVersion
from avtalsagent.ingestion.checks import coverage, missing_text, org_numbers
from avtalsagent.ingestion.checks.coverage import CoverageStatus
from avtalsagent.ingestion.extract.title_matcher import TITLE_RULE, MatchStats
from avtalsagent.ingestion.parsers.docling_parser import DoclingParser
from avtalsagent.ingestion.pipeline import IngestionResult, link_infos, process
from avtalsagent.ingestion.report import (
    RunInfo,
    build_report,
    render_markdown,
    render_report,
    write_report,
)
from avtalsagent.ingestion.step2_parse import ParseStatus, SourceFile, parse_files
from avtalsagent.ingestion.step5_validate import AcceptedFinding
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register

NBSP = " "

AREA = "Bemanningstjänster"
PROCUREMENT = "23.3-14537-2023"
AGREEMENT = "23.3-14537-2023-001"  # A Hub Group AB, 559199-9601, in the sample register
WRONG_ORG = "556677-8899"  # made up; a valid check digit, in no register row
PAGE_URL = (
    "https://www.avropa.se/ramavtal/ramavtalsomraden/konsulttjanster---bemanning-och-"
    "rekrytering/bemanningstjanster/bemanningstjanster---it-tjanster-upp-till-1000-timmar/"
)
PAGE_TITLE = "Bemanningstjänster - IT-tjänster upp till 1000 timmar"
FILE_URL = (
    "https://www.avropa.se/globalassets/bilagor/1.-aktuella-rao/bemanningstjanster-2023/"
    "1.-avropsstod/ramavtalets-huvuddokument.pdf"
)
REGISTER_VERSION = RegisterVersion(list_date=date(2026, 10, 5), title="Giltiga ramavtal 2026-10-05")

PARTY_CLAUSE = (
    "Ramavtal med avtalsnummer [X], har träffats för Avropsberättigades räkning, mellan "
    "Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829, nedan "
    f"Kammarkollegiet, och Exempelleverantören AB, organisationsnummer {WRONG_ORG}, nedan "
    "Ramavtalsleverantören."
)
REFERENCE = "Ramavtalsleverantören ska uppfylla kraven på miljö och hållbarhet, enligt punkt 3.1."
REQUIREMENT = "Ramavtalsleverantören ska ha ett miljöledningssystem."

# The document, as step 2 reads it: (kind, text, page).
CONTENT = (
    (BlockKind.HEADING, "1 Parter", 1),
    (BlockKind.TEXT, PARTY_CLAUSE, 1),
    (BlockKind.HEADING, "2 Ramavtalets omfattning", 1),
    (BlockKind.TEXT, REFERENCE, 1),
    (BlockKind.HEADING, "3 Krav", 2),
    (BlockKind.HEADING, "3.1 Miljö och hållbarhet", 2),
    (BlockKind.TEXT, REQUIREMENT, 2),
)

MAIN = "ab" * 32  # the hand-made main document's hash


@pytest.fixture
def register(sample_register_xlsx: Path) -> list[RegisterEntry]:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows
    return [RegisterEntry.from_row(row) for row in rows]


def parsed_document(sha256: str = MAIN) -> ParsedDocument:
    """The main document as Docling reads the PDF of the last test."""
    return ParsedDocument(
        sha256=sha256,
        file_type="pdf",
        parser="hand-made",
        pages=(
            PageInfo(number=1, char_count=350, needs_ocr=False),
            PageInfo(number=2, char_count=75, needs_ocr=False),
        ),
        blocks=tuple(Block(kind=kind, text=text, page=page) for kind, text, page in CONTENT),
    )


def main_link(sha256: str = MAIN) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=FILE_URL,
        title="Ramavtalets huvuddokument",
        category="Avtal",
        agreement_number=None,
        site_updated=None,
        page_url=PAGE_URL,
        page_title=PAGE_TITLE,
        page_procurement_numbers=(PROCUREMENT,),
        page_period="2025-04-03 - 2029-04-02",
    )


def run_info() -> RunInfo:
    return RunInfo(
        started_at=datetime(2026, 10, 6, 14, 3, 12, tzinfo=UTC),
        finished_at=datetime(2026, 10, 6, 14, 7, 24, tzinfo=UTC),
        register_version=REGISTER_VERSION,
        areas=(AREA,),
        model=None,
    )


def part(markdown: str, heading: str) -> str:
    """One "## " part of the report, from its heading to the next."""
    start = markdown.index(f"\n## {heading}\n")
    end = markdown.find("\n## ", start + 1)
    return markdown[start : end if end != -1 else len(markdown)]


def org_finding(result: IngestionResult, sha256: str = MAIN) -> Finding:
    """The finding for the made-up organisation number in the file."""
    [finding] = [
        f for f in result.validation.findings if f.check == org_numbers.CHECK and f.sha256 == sha256
    ]
    return finding


# --- steps 3-5 on a hand-made parse ------------------------------------------------------


def test_a_file_with_an_org_number_of_no_supplier_is_held_back(
    register: list[RegisterEntry],
) -> None:
    result = process([parsed_document()], [main_link()], register, [AREA], {})

    validation = result.validation
    assert [(f.check, f.sha256, f.subject, f.severity) for f in validation.findings] == [
        (org_numbers.CHECK, MAIN, WRONG_ORG, Severity.QUARANTINE),
        (coverage.CHECK, None, PROCUREMENT, Severity.REPORT),
    ]
    assert validation.quarantine.files == {MAIN}
    assert not validation.quarantine.sections
    # The register agreement's only main document is held back, so it is not covered.
    [agreement] = validation.coverage
    assert (agreement.agreement_number, agreement.status) == (AGREEMENT, CoverageStatus.HELD_BACK)
    assert agreement.held_back == (MAIN,)


def test_the_reference_is_resolved_to_a_section_of_the_same_file(
    register: list[RegisterEntry],
) -> None:
    result = process([parsed_document()], [main_link()], register, [AREA], {})

    [reference] = result.corpus.references
    assert (reference.mention.raw, reference.mention.kind) == (
        "punkt 3.1",
        ReferenceKind.SECTION_NUMBER,
    )
    assert (reference.status, reference.rule) == (ReferenceStatus.RESOLVED, "R1")
    [target] = reference.targets
    sections = {section.position: section for section in result.chunked[0].sections}
    assert target.sha256 == MAIN
    assert target.section is not None
    assert sections[target.section].number == "3.1"
    assert result.corpus.rules_rate == 1.0
    assert result.corpus.match_stats is None  # no matcher given


def test_an_accepted_finding_releases_the_file_which_then_covers_the_agreement(
    register: list[RegisterEntry],
) -> None:
    # The key as a person copies it from the report of a run without the acceptance.
    key = org_finding(process([parsed_document()], [main_link()], register, [AREA], {})).key
    acceptance = AcceptedFinding.model_validate(
        {
            "key": key,
            "reason": "Påhittat skäl för testet.",
            "reviewer": "Testa Testsson",
            "date": date(2026, 10, 7),
        }
    )
    accepted = {acceptance.key: acceptance}

    validation = process([parsed_document()], [main_link()], register, [AREA], accepted).validation

    [finding] = validation.findings  # the coverage finding is gone
    assert (finding.key, finding.accepted_reason) == (key, "Påhittat skäl för testet.")
    assert not validation.quarantine.files
    [agreement] = validation.coverage
    assert agreement.status is CoverageStatus.COVERED
    assert agreement.main_documents == (MAIN,)
    assert not validation.unused_acceptances


def test_a_parsed_file_no_page_links_to_is_left_out(register: list[RegisterEntry]) -> None:
    gone = "cd" * 32
    result = process(
        [parsed_document(), parsed_document(gone)], [main_link()], register, [AREA], {}
    )

    assert result.unlinked == [gone]
    assert [document.sha256 for document in result.parsed] == [MAIN]
    assert [document.sha256 for document in result.chunked] == [MAIN]
    assert [e.metadata.sha256 for e in result.corpus.extractions] == [MAIN]
    assert set(result.validation.number_status) == {MAIN}


def test_a_link_to_a_file_that_was_not_parsed_holds_that_file_back(
    register: list[RegisterEntry],
) -> None:
    unparsed = "ef" * 32
    links = [main_link(), main_link(unparsed).model_copy(update={"title": "Allmänna villkor"})]

    result = process([parsed_document()], links, register, [AREA], {})

    validation = result.validation
    not_parsed = [f for f in validation.findings if f.sha256 == unparsed]
    assert [(f.check, f.subject, f.severity) for f in not_parsed] == [
        (missing_text.CHECK, missing_text.NOT_PARSED, Severity.QUARANTINE)
    ]
    assert validation.quarantine.files == {MAIN, unparsed}
    # Kept in the result too, so the report can name the pages of every link.
    assert result.links == links


def test_the_context_header_names_the_register_area_and_a_card_its_supplier(
    register: list[RegisterEntry],
) -> None:
    result = process([parsed_document()], [main_link()], register, [AREA], {})
    header = result.chunked[0].chunks[0].context_header
    assert header.startswith(f"{AREA} ({PROCUREMENT}) › Ramavtalets huvuddokument")

    card = main_link("12" * 32).model_copy(
        update={"title": "Ramavtal", "agreement_number": AGREEMENT}
    )
    [info] = link_infos({card.sha256: [card]}, register)[card.sha256]
    assert (info.framework_areas, info.supplier_name) == ((AREA,), "A Hub Group AB")


def test_numbers_are_kept_in_the_register_spelling(register: list[RegisterEntry]) -> None:
    # The register writes 23.3-2965-20:001 (AB Svenska Pass); its key is
    # 23.3-2965-2020-001. The card's text and link write it as 185872a6bb90 §1.3.1 does.
    card = "34" * 32
    text = (
        "Ramavtal med avtalsnummer 23.3.2965-20:001, har träffats för Avropsberättigades räkning."
    )
    document = parsed_document(card).model_copy(
        update={"blocks": (Block(kind=BlockKind.TEXT, text=text, page=1),)}
    )
    link = main_link(card).model_copy(
        update={
            "title": "Ramavtal",
            "agreement_number": "23.3.2965-20:001",
            "page_procurement_numbers": ("23.3-2965-20",),
        }
    )

    [extraction] = process([document], [link], register, [], {}).corpus.extractions

    assert extraction.metadata.agreement_number == "23.3-2965-20:001"
    assert [f.value for f in extraction.facts if f.kind is FactKind.AGREEMENT_NUMBER] == [
        "23.3-2965-20:001"
    ]


# A section title no heading has, as ADR 0009 quotes "Försäljningsredovisning och
# administrativ avgift" for "9.15 Försäljningsredovisning och administrationsavgift".
TITLE_REFERENCE = "Kraven framgår av avsnitt Miljökrav och hållbarhet."


def choose_environment(phrase: str, context: str, candidates: Sequence[str]) -> str | None:
    """A stand-in for the language model: the heading on the environment, if offered."""
    return "3.1 Miljö och hållbarhet" if "3.1 Miljö och hållbarhet" in candidates else None


def test_a_title_the_rules_do_not_find_goes_to_the_matcher(register: list[RegisterEntry]) -> None:
    document = parsed_document()
    document = document.model_copy(
        update={
            "blocks": (*document.blocks, Block(kind=BlockKind.TEXT, text=TITLE_REFERENCE, page=2))
        }
    )

    corpus = process([document], [main_link()], register, [AREA], {}, choose_environment).corpus

    assert [(r.mention.raw, r.status, r.rule) for r in corpus.references] == [
        ("punkt 3.1", ReferenceStatus.RESOLVED, "R1"),
        ("avsnitt Miljökrav och hållbarhet", ReferenceStatus.RESOLVED, TITLE_RULE),
    ]
    assert corpus.match_stats == MatchStats(asked=1, answered=1)
    assert corpus.rules_rate == 0.5  # the title is not resolved by the rules alone


# --- the report of the run ---------------------------------------------------------------


def assert_report_shows_quarantine_coverage_and_rate(result: IngestionResult, sha256: str) -> str:
    """M4's three done-when criteria (ADR 0009, Kontext) in the report; returns the markdown.

    The file held back with its finding's key, the agreement it leaves without a
    main document, and the share of resolved references.
    """
    report = build_report(result, run_info())
    markdown = render_markdown(report)

    assert [held.file.sha256 for held in report.quarantine.files] == [sha256]
    held = part(markdown, "Karantän")
    assert f"Ramavtalets huvuddokument (`{sha256[:12]}`)" in held
    assert f"Nyckel: `{org_finding(result, sha256).key}`" in held

    [group] = report.coverage.groups
    assert [gap.agreement_number for gap in group.agreements] == [AGREEMENT]
    covered = part(markdown, "Täckning")
    assert f"| {AREA} | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |" in covered
    assert (
        f"- **Bara dokument i karantän: {AREA}, 1 avtal** ({AGREEMENT}, A Hub Group AB): "
        f"Ramavtalets huvuddokument (`{sha256[:12]}`), i karantän, på sidan {PAGE_TITLE}."
    ) in covered

    assert report.references.rate == 1.0
    assert f"= 1 / (1 − 0 − 0 − 0 − 0 − 0) = 1 / 1 = **100,0{NBSP}%**" in part(
        markdown, "Hänvisningar"
    )
    return markdown


def test_the_report_shows_the_file_in_quarantine_the_coverage_gap_and_the_rate(
    register: list[RegisterEntry], tmp_path: Path
) -> None:
    result = process([parsed_document()], [main_link()], register, [AREA], {})
    markdown = assert_report_shows_quarantine_coverage_and_rate(result, MAIN)

    files = render_report(build_report(result, run_info()))
    written, json = write_report(files, tmp_path / "reports")
    assert written.name == "2026-10-06T140312-inlasning.md"
    assert written.read_text(encoding="utf-8") == markdown
    assert f'"{org_finding(result).key}"' in json.read_text(encoding="utf-8")


def test_the_report_gives_the_rate_with_and_without_the_language_model(
    register: list[RegisterEntry],
) -> None:
    document = parsed_document()
    document = document.model_copy(
        update={
            "blocks": (*document.blocks, Block(kind=BlockKind.TEXT, text=TITLE_REFERENCE, page=2))
        }
    )
    result = process([document], [main_link()], register, [AREA], {}, choose_environment)
    run = replace(run_info(), model="gpt-6-luna")

    references = part(render_markdown(build_report(result, run)), "Hänvisningar")

    assert f"= 2 / 2 = **100,0{NBSP}%**" in references
    assert f"- **Bara med regler:** 50,0{NBSP}%." in references


# --- the whole chain, from a PDF ---------------------------------------------------------


def make_pdf(target: Path) -> Path:
    """`CONTENT` as a PDF: headings in a heading style, page 2 after a page break."""
    styles = getSampleStyleSheet()
    story: list[Flowable] = []
    for kind, text, page in CONTENT:
        if page == 2 and not any(isinstance(item, PageBreak) for item in story):
            story.append(PageBreak())
        if kind is BlockKind.HEADING:
            number = text.split()[0]
            story.append(Paragraph(text, styles["Heading3" if "." in number else "Heading2"]))
        else:
            story.append(Paragraph(text, styles["Normal"]))
    SimpleDocTemplate(str(target), pagesize=A4).build(story)
    return target


def test_a_pdf_parsed_with_docling_goes_through_steps_3_to_5_into_the_report(
    register: list[RegisterEntry], tmp_path: Path
) -> None:
    pdf = make_pdf(tmp_path / "ramavtalets-huvuddokument.pdf")
    sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
    try:
        [parse] = parse_files(
            [SourceFile(sha256, "pdf", pdf)], DoclingParser(), tmp_path / "parsed"
        )
        if parse.document is None:
            raise RuntimeError(parse.message)
    except Exception as error:  # the models could not be downloaded
        if os.environ.get("AVTALSAGENT_REQUIRE_MODELS") == "1":
            raise
        pytest.skip(f"Docling's models are not available: {error}")
    assert parse.status is ParseStatus.PARSED

    result = process([parse.document], [main_link(sha256)], register, [AREA], {})

    assert [section.heading for section in result.chunked[0].sections] == [
        "1 Parter",
        "2 Ramavtalets omfattning",
        "3 Krav",
        "3.1 Miljö och hållbarhet",
    ]
    assert result.validation.quarantine.files == {sha256}
    assert_report_shows_quarantine_coverage_and_rate(result, sha256)
