"""Tests for avtalsagent.ingestion.__main__, the command line.

The commands that read or write the database (`fetch`, `parse`, `process`,
`run`, `outline`) are not run here: there is no database in the unit tests,
and they only pass values between the steps. What they decide on their own is
tested: the commands and options, the check of the run's framework areas, which
files have a parse result, and the log line of each step. The findings, hashes
and numbers are those of the pilot run (M4, 2026-10-05 register): Microsoft
Ireland's organisation number in 171a3cacf5fd, the scanned section 7.16 of
e04bad6a0ced, and 6765/05 (IBM), whose main document step 1 does not fetch.
"""

import logging
from collections import Counter
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Fact,
    FactKind,
    Finding,
    Quarantine,
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
    Severity,
)
from avtalsagent.domain.parsed import Block, BlockKind, Chunk, ParsedDocument, Section
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.__main__ import (
    build_parser,
    check_areas,
    configure_logging,
    counts_text,
    load_parsed,
    step_lines,
)
from avtalsagent.ingestion.catalog import UnknownAreaError
from avtalsagent.ingestion.checks.coverage import AgreementCoverage, CoverageStatus
from avtalsagent.ingestion.extract.title_matcher import MatchStats
from avtalsagent.ingestion.pipeline import IngestionResult
from avtalsagent.ingestion.step2_parse import SourceFile, cache_path
from avtalsagent.ingestion.step3_chunk import ChunkedDocument, OutlineKind
from avtalsagent.ingestion.step4_extract import CorpusExtraction
from avtalsagent.ingestion.step5_validate import AcceptedFinding, Validation

MICROSOFT_MAIN = "171a3cacf5fd42094d4d46bf49211224476200ca1dae00227d9c55e85df2b9d3"
SCANNED = "e04bad6a0ced579b2cfaf5953c6edd5547acc4e5657cb1097d0d8e189f0a498b"
UNLINKED = "0" * 64


# --- Commands ---------------------------------------------------------------------------------


class TestCommands:
    def test_process_takes_the_areas_of_the_run(self) -> None:
        args = build_parser().parse_args(
            ["process", "--area", "IT-drift", "--area", "Bemanningstjänster"]
        )

        assert args.step == "process"
        assert args.area == ["IT-drift", "Bemanningstjänster"]

    def test_run_without_areas_leaves_them_to_the_settings(self) -> None:
        args = build_parser().parse_args(["run"])

        assert args.step == "run"
        assert args.area is None

    def test_fetch_still_takes_areas(self) -> None:
        assert build_parser().parse_args(["fetch", "--area", "IT-drift"]).area == ["IT-drift"]

    def test_chunk_is_replaced_by_process(self) -> None:
        # Step 3 alone would store sections without their checks (M4 design §4).
        with pytest.raises(SystemExit):
            build_parser().parse_args(["chunk"])

    def test_parse_takes_no_areas(self) -> None:
        # Step 2 parses every downloaded file; the areas choose what step 1 downloads.
        with pytest.raises(SystemExit):
            build_parser().parse_args(["parse", "--area", "IT-drift"])


# --- The run's areas --------------------------------------------------------------------------


def entry(framework_area: str) -> RegisterEntry:
    return RegisterEntry(
        agreement_number="23.3-5890-2023-003",
        procurement_number="23.3-5890-2023",
        org_number="556372-4425",
        supplier_name="Advania Sverige AB",
        former_supplier_name=None,
        framework_area=framework_area,
        sub_area_path=(framework_area, "IT-drift Mindre, upp till 200 anställda"),
        valid_from=date(2024, 11, 14),
        valid_to=date(2028, 11, 13),
        max_extension_to=None,
    )


class TestCheckAreas:
    def test_an_area_of_the_register_passes(self) -> None:
        check_areas(["IT-drift"], [entry("IT-drift")])

    def test_an_area_the_register_lacks_is_refused(self) -> None:
        # Coverage would look for no agreement of it and report nothing.
        with pytest.raises(UnknownAreaError, match="not in the register: IT drift.*IT-drift"):
            check_areas(["IT-drift", "IT drift"], [entry("IT-drift")])

    def test_an_empty_register_is_refused(self) -> None:
        with pytest.raises(UnknownAreaError, match="register is empty"):
            check_areas(["IT-drift"], [])


# --- The parse cache --------------------------------------------------------------------------


def parsed(sha256: str) -> ParsedDocument:
    return ParsedDocument(
        sha256=sha256,
        file_type="pdf",
        parser="docling 2.133.0",
        pages=(),
        blocks=(Block(kind=BlockKind.TEXT, text="Ramavtal", page=1),),
    )


def test_files_without_a_parse_result_are_returned_apart(tmp_path: Path) -> None:
    cache_path(tmp_path, MICROSOFT_MAIN).write_text(parsed(MICROSOFT_MAIN).model_dump_json())
    files = [
        SourceFile(MICROSOFT_MAIN, "pdf", tmp_path / f"{MICROSOFT_MAIN}.pdf"),
        SourceFile(SCANNED, "pdf", tmp_path / f"{SCANNED}.pdf"),
    ]

    documents, unparsed = load_parsed(files, tmp_path)

    assert [document.sha256 for document in documents] == [MICROSOFT_MAIN]
    assert unparsed == [files[1]]


# --- Log lines --------------------------------------------------------------------------------


def chunked(sha256: str, outline: OutlineKind, sections: int, chunks: int) -> ChunkedDocument:
    return ChunkedDocument(
        sha256=sha256,
        outline=outline,
        sections=[
            Section(
                position=position,
                number=str(position),
                title="Avtalsbrott och påföljder",
                level=1,
                parent=None,
                path=(),
                page_start=1,
                page_end=1,
                text="",
            )
            for position in range(sections)
        ],
        chunks=[
            Chunk(section=0, position=position, context_header="", text="")
            for position in range(chunks)
        ],
        contents_missing=None,
    )


def reference(kind: ReferenceKind, status: ReferenceStatus, rule: str | None) -> Reference:
    return Reference(
        sha256=SCANNED,
        mention=ReferenceMention(
            section=1, start=0, end=10, raw="avsnitt X", kind=kind, key="X", rule="R4"
        ),
        status=status,
        rule=rule,
    )


def extraction(sha256: str, facts: int) -> DocumentExtraction:
    return DocumentExtraction(
        metadata=DocumentMetadata(
            sha256=sha256,
            title="Volymavtalets huvudavtal 1.0",
            document_type=DocumentType.MAIN_DOCUMENT,
            type_rule="R05",
            agreement_number=None,
            annex_number=None,
            first_chapter=None,
            tendsign_cover=None,
            is_template=False,
            version_date=None,
            version_rule=None,
            published_on=None,
            site_updated=None,
        ),
        facts=tuple(
            Fact(
                kind=FactKind.ORG_NUMBER,
                value="502052-1307",
                raw="502052-1307",
                rule="ORG",
                block=index,
                page=1,
            )
            for index in range(facts)
        ),
        mentions=(),
    )


ORG_FINDING = Finding(
    check="org_numbers",
    severity=Severity.QUARANTINE,
    subject="502052-1307",
    message="Organisationsnumret 502052-1307 (s. 1), som dokumentet anger för Microsoft Ireland "
    "Operations Ltd, tillhör ingen leverantör på upphandlingen på sidan som länkar till "
    "dokumentet (23.5-3718-2024).",
    sha256=MICROSOFT_MAIN,
)
SECTION_FINDING = Finding(
    check="missing_text",
    severity=Severity.QUARANTINE,
    subject="7.16",
    message="Avsnitt 7.16, s. 14-22, omfattar sidorna 15-21 som saknar textlager.",
    sha256=SCANNED,
    section=1,
)
SCANNED_NOTE = Finding(
    check="missing_text",
    severity=Severity.NOTE,
    subject="s. 1, 6-13, 15-21, 24-25, 27-30",
    message="22 av dokumentets 31 sidor saknar textlager (ingen OCR körs).",
    sha256=SCANNED,
)
ACCEPTED = AcceptedFinding.model_validate(
    {
        "key": "org_numbers:" + "1" * 64 + ":556866-4444",
        "reason": "Påhittat godkännande för testet.",
        "reviewer": "Testare",
        "date": date(2026, 10, 7),
    }
)


def coverage(number: str, status: CoverageStatus) -> AgreementCoverage:
    return AgreementCoverage(
        agreement_number=number,
        procurement_number=number,
        framework_area="Programvaror och tjänster",
        supplier_name="IBM Svenska AB",
        status=status,
        cards=(),
        main_documents=(),
        not_counted=(),
    )


def result(match_stats: MatchStats | None) -> IngestionResult:
    accepted_note = SCANNED_NOTE.model_copy(update={"accepted_reason": "Skannade bilagor."})
    return IngestionResult(
        parsed=[parsed(MICROSOFT_MAIN), parsed(SCANNED)],
        chunked=[
            chunked(MICROSOFT_MAIN, OutlineKind.NUMBERED, sections=3, chunks=4),
            chunked(SCANNED, OutlineKind.NUMBERED, sections=2, chunks=2),
        ],
        unlinked=[UNLINKED],
        corpus=corpus(match_stats),
        validation=Validation(
            findings=[ORG_FINDING, SECTION_FINDING, accepted_note],
            quarantine=Quarantine(
                files=frozenset({MICROSOFT_MAIN}), sections=frozenset({(SCANNED, 1)})
            ),
            coverage=[
                coverage("23.5-3718-2024", CoverageStatus.HELD_BACK),
                coverage("6765/05", CoverageStatus.NOT_COVERED),
            ],
            number_status={},
            unused_acceptances=[ACCEPTED],
        ),
        links=[],
    )


def corpus(match_stats: MatchStats | None) -> CorpusExtraction:
    return CorpusExtraction(
        extractions=[extraction(MICROSOFT_MAIN, 2), extraction(SCANNED, 1)],
        references=[
            reference(ReferenceKind.SECTION_TITLE, ReferenceStatus.RESOLVED, "R4"),
            reference(ReferenceKind.SECTION_TITLE, ReferenceStatus.RESOLVED, "R4-llm"),
            reference(ReferenceKind.SECTION_TITLE, ReferenceStatus.TITLE_MISSING, "R4"),
            reference(ReferenceKind.SECTION_NUMBER, ReferenceStatus.SELF, None),
            reference(ReferenceKind.LAW, ReferenceStatus.EXTERNAL, None),
        ],
        rules_rate=1 / 3,
        match_stats=match_stats,
    )


class TestStepLines:
    def test_one_line_per_step_with_its_counts(self) -> None:
        lines = step_lines(result(MatchStats(asked=2, answered=1, calls=1, cache_hits=1)))

        assert lines == [
            "Step 3: 2 files, 5 sections, 6 chunks (numbered 2, questions 0, headings 0, none 0); "
            "left out, no page links to them: 1",
            # SELF and LAW are not in the rate (M4 design, decision 5): 2 of 3.
            "Step 4: 3 facts, 5 references; resolved 2 of 3 counted (66.7%), by the rules alone "
            "33.3%; language model: asked 2, answered 1, calls 1, from the cache 1",
            "Step 5: 3 findings (quarantine 2, report 0, note 0, accepted 1); quarantine: files 1, "
            "sections 1; agreements of the run's areas: 2 (covered 0, procurement_version 0, "
            "held_back 1, not_covered 1); "
            "acceptances that match no finding: 1",
        ]

    def test_a_run_without_the_language_model_says_so(self) -> None:
        assert step_lines(result(None))[1].endswith("; language model not used")


def test_counts_are_given_for_every_kind_in_order() -> None:
    counts = Counter({Severity.NOTE: 9, Severity.QUARANTINE: 15})

    assert counts_text(counts, Severity) == "quarantine 15, report 0, note 9"


# --- Logging ----------------------------------------------------------------------------------


@pytest.fixture
def package_logger() -> Iterator[logging.Logger]:
    """The package's logger; the logging set-up is put back after the test."""
    root, logger = logging.getLogger(), logging.getLogger("avtalsagent")
    handlers, root_level, level = list(root.handlers), root.level, logger.level
    yield logger
    root.handlers[:] = handlers
    root.setLevel(root_level)
    logger.setLevel(level)


class TestConfigureLogging:
    def test_the_package_logs_at_the_configured_level(self, package_logger: logging.Logger) -> None:
        configure_logging("debug")

        assert logging.getLogger("avtalsagent.ingestion").isEnabledFor(logging.DEBUG)

    def test_libraries_log_warnings_only(self, package_logger: logging.Logger) -> None:
        # httpx logs every request at INFO; fetch makes hundreds.
        configure_logging("INFO")

        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)

    def test_an_unknown_level_fails_at_start(self, package_logger: logging.Logger) -> None:
        with pytest.raises(ValueError, match="Unknown level"):
            configure_logging("LOUD")
