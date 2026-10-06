"""Tests for avtalsagent.ingestion.__main__, the command line.

The commands that read or write the database (`fetch`, `parse`, `process`,
`run`, `outline`) mostly pass values between the steps, and there is no
database in the unit tests. What they decide on their own is tested: the
commands and options, the check of the run's framework areas, which files have
a parse result, and the log line of each step. `process` is also run with
stand-ins for the database: the report is rendered before the save and written
after it, and a missing accepted findings file is logged. The findings, hashes
and numbers are those of the pilot run (M4, 2026-10-05 register): Microsoft
Ireland's organisation number in 171a3cacf5fd, the scanned section 7.16 of
e04bad6a0ced, and 6765/05 (IBM), whose main document step 1 does not fetch.
"""

import logging
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from avtalsagent.config import Settings
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
from avtalsagent.domain.parsed import Block, BlockKind, Chunk, PageInfo, ParsedDocument, Section
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion import __main__ as cli
from avtalsagent.ingestion.__main__ import (
    build_parser,
    check_areas,
    configure_logging,
    counts_text,
    load_parsed,
    process,
    step_lines,
)
from avtalsagent.ingestion.catalog import UnknownAreaError
from avtalsagent.ingestion.checks.coverage import AgreementCoverage, CoverageStatus
from avtalsagent.ingestion.extract.title_matcher import MatchStats
from avtalsagent.ingestion.llm_title_matcher import LanguageModelError
from avtalsagent.ingestion.pipeline import IngestionResult
from avtalsagent.ingestion.report import build_report, write_report
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
        # Step 3 alone would store sections without their checks (ADR 0009 decision 11).
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
        "key": "org_numbers:quarantine:" + "1" * 64 + ":556866-4444",
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
            # SELF and LAW are not in the rate (ADR 0009 decision 9): 2 of 3.
            "Step 4: 3 facts, 5 references; resolved 2 of 3 counted (66.7%), by the rules alone "
            "33.3%; language model: asked 2, answered 1, calls 1, from the cache 1",
            "Step 5: 3 findings (quarantine 2, report 0, note 0, accepted 1); quarantine: files 1, "
            "sections 1; agreements of the run's areas: 2 (covered 0, procurement_version 0, "
            "held_back 1, not_covered 1); "
            "acceptances that match no finding: 1",
        ]

    def test_a_run_without_the_language_model_says_so(self) -> None:
        assert step_lines(result(None))[1].endswith("; language model not used")

    def test_the_pages_of_an_e_signature_certificate_are_counted(self) -> None:
        # 185872a6bb90 p18, the certificate after the last page of a signed card.
        certificate = [
            "Signaturerna i detta dokument är juridiskt bindande.",
            "AbCdEfGhIjKlMnOpQrStUv 2023-02-22 15:14",
            "Dokumentet är skyddat med ett Adobe CDS-certifikat.",
        ]
        card = parsed(MICROSOFT_MAIN).model_copy(
            update={
                "pages": tuple(
                    PageInfo(number=n, char_count=900, needs_ocr=False) for n in (17, 18)
                ),
                "blocks": (
                    Block(kind=BlockKind.TEXT, text="Ramavtal", page=17),
                    *(Block(kind=BlockKind.TEXT, text=text, page=18) for text in certificate),
                ),
            }
        )
        signed = result(None)
        signed.parsed[0] = card

        assert step_lines(signed)[0].endswith(
            "; left out, no page links to them: 1; "
            "e-signature certificates left out: files 1, pages 1"
        )
        assert "certificates" not in step_lines(result(None))[0]


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
        # httpx logs every request at INFO; fetch makes hundreds. pytest has put handlers on
        # the root logger, which would make basicConfig do nothing: without them, as when
        # the command starts (the fixture puts them back).
        logging.getLogger().handlers.clear()

        configure_logging("INFO")

        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert root.handlers  # the package's lines are printed
        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
        assert logging.getLogger("avtalsagent.ingestion").isEnabledFor(logging.INFO)

    def test_an_unknown_level_fails_at_start(self, package_logger: logging.Logger) -> None:
        with pytest.raises(ValueError, match="Unknown level"):
            configure_logging("LOUD")


# --- process: the save and the report ---------------------------------------------------------


class FakeDatabase:
    """Stands in for `session_factory(...)` and records what `process` does, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    @contextmanager
    def __call__(self) -> Iterator[None]:  # a session that only reads
        yield None

    @contextmanager
    def begin(self) -> Iterator[None]:  # a transaction: committed when the block ends
        yield None
        self.events.append("commit")


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeDatabase:
    """`process` on an IT-drift register with no files, its database and model replaced."""
    db = FakeDatabase()
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        accepted_findings_file=tmp_path / "accepted_findings.toml",
        openai_api_key=None,
    )

    def record(event: str, wrapped: Any = None) -> Any:
        def step(*args: Any) -> Any:
            db.events.append(event)
            return wrapped(*args) if wrapped else None

        return step

    replaced: dict[str, Any] = {
        "get_settings": lambda: settings,
        "create_db_engine": lambda: None,
        "session_factory": lambda engine: db,
        "source_files": lambda session, data_dir: [],
        "catalog_links": lambda session: [],
        "register_entries": lambda session: [entry("IT-drift")],
        "register_version": lambda session: None,
        "openai_title_matcher": lambda settings: None,
        "save_sections": record("save_sections"),
        "save_extraction": record("save_extraction"),
        "save_findings": record("save_findings"),
        "build_report": record("build_report", build_report),
        "write_report": record("write_report", write_report),
    }
    for name, value in replaced.items():
        monkeypatch.setattr(cli, name, value)
    return db


class TestProcess:
    def test_the_report_is_built_before_the_save_and_written_after_it(
        self, database: FakeDatabase, tmp_path: Path
    ) -> None:
        process(["IT-drift"])

        assert database.events == [
            "build_report",
            "save_sections",
            "save_extraction",
            "save_findings",
            "commit",
            "write_report",
        ]
        assert len(list((tmp_path / "data" / "reports").glob("*-inlasning.*"))) == 2

    def test_a_report_that_fails_saves_nothing(
        self, database: FakeDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed run leaves the previous run's results in place, also when the report fails.
        def broken(*args: Any) -> None:
            database.events.append("build_report")
            raise RuntimeError("a finding the report cannot render")

        monkeypatch.setattr(cli, "build_report", broken)

        with pytest.raises(RuntimeError, match="cannot render"):
            process(["IT-drift"])

        assert database.events == ["build_report"]

    def test_a_missing_accepted_findings_file_is_logged(
        self, database: FakeDatabase, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A mistyped ACCEPTED_FINDINGS_FILE would otherwise accept nothing without a word.
        with caplog.at_level(logging.WARNING, logger="avtalsagent.ingestion"):
            process(["IT-drift"])

        [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning.getMessage().startswith("No accepted findings file at ")
        assert warning.getMessage().endswith("accepted_findings.toml, so no deviation is accepted")

    def test_an_accepted_findings_file_without_entries_is_not_warned_about(
        self, database: FakeDatabase, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        (tmp_path / "accepted_findings.toml").write_text("# Inga godkännanden.\n")

        with caplog.at_level(logging.WARNING, logger="avtalsagent.ingestion"):
            process(["IT-drift"])

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_language_model_error_stops_the_command_with_its_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(areas: list[str]) -> None:
        raise LanguageModelError("the language model gpt-6-luna could not be asked (APIError)")

    monkeypatch.setattr(cli, "process", fail)
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr("sys.argv", ["avtalsagent.ingestion", "process", "--area", "IT-drift"])

    with (
        caplog.at_level(logging.ERROR, logger="avtalsagent.ingestion"),
        pytest.raises(SystemExit) as stopped,
    ):
        cli.main()

    assert stopped.value.code == 1
    assert caplog.messages == [
        "process: the language model gpt-6-luna could not be asked (APIError)"
    ]
