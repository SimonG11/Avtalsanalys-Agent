"""Tests for avtalsagent.ingestion.__main__, the command line.

The commands that read or write the database (`fetch`, `parse`, `process`,
`index`, `run`, `search`, `outline`) mostly pass values between the steps, and
there is no database in the unit tests. What they decide on their own is
tested: the commands and options, the check of the run's framework areas,
which files have a parse result, and the log line of each step. `process` and
`index` are also run with stand-ins for the database: the report is rendered
before the save and written after it, the save empties the search index, a
missing accepted findings file is logged, step 6 embeds only what the cache
lacks and caches each batch before the index is saved, and a command without
OPENAI_API_KEY stops with a message, as `search` does for a blank question;
argparse refuses a `--limit` below 1. The findings, hashes and numbers are
those of the pilot run (M4, 2026-10-05 register): Microsoft Ireland's
organisation number in 171a3cacf5fd, the scanned section 7.16 of
e04bad6a0ced, and 6765/05 (IBM), whose main document step 1 does not fetch.
The chunk texts of step 6 are 0a5491b1398e p. 24, IT-drift Mindre's penalty
clause 6.21.4.
"""

import logging
from collections import Counter
from collections.abc import Iterator, Sequence
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
from avtalsagent.domain.search import ChunkKey, DocumentScope, IndexPlan, SearchFilters
from avtalsagent.ingestion import __main__ as cli
from avtalsagent.ingestion.__main__ import (
    CommandError,
    build_parser,
    check_areas,
    configure_logging,
    counts_text,
    hit_lines,
    index,
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
from avtalsagent.ingestion.step6_index import IndexSource, text_hash
from avtalsagent.retrieval import swedish_text
from avtalsagent.retrieval.bm25 import TermIndex
from avtalsagent.retrieval.embedder import EmbeddingError
from avtalsagent.retrieval.hybrid_search import IndexNotReadyError, SectionCopy, SectionHit

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

    def test_index_is_step_6_and_takes_no_areas(self) -> None:
        # The index holds every chunk the quarantine lets through; a search filters it.
        assert build_parser().parse_args(["index"]).step == "index"
        with pytest.raises(SystemExit):
            build_parser().parse_args(["index", "--area", "IT-drift"])

    def test_search_takes_a_question_an_area_an_agreement_and_a_limit(self) -> None:
        args = build_parser().parse_args(
            [
                "search",
                "Hur stort är vitet om bristen kvarstår?",
                "--area",
                "IT-drift",
                "--agreement",
                "23.3-5890-2023-003",
                "--limit",
                "3",
            ]
        )

        assert args.question == "Hur stort är vitet om bristen kvarstår?"
        assert (args.framework_area, args.agreement_number, args.limit) == (
            "IT-drift",
            "23.3-5890-2023-003",
            3,
        )
        # Not `area`: that would be the run's list of areas of fetch, process and run.
        assert not hasattr(args, "area")

    def test_search_without_options_leaves_the_limit_to_the_settings(self) -> None:
        args = build_parser().parse_args(["search", "Vad gäller vid prisjustering?"])

        assert (args.framework_area, args.agreement_number, args.limit) == (None, None, None)
        assert args.document_type is None

    def test_search_takes_a_document_type_of_step_4(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = build_parser().parse_args(["search", "Vite?", "--type", "general_terms"])

        assert args.document_type == "general_terms"
        with pytest.raises(SystemExit):
            build_parser().parse_args(["search", "Vite?", "--type", "Allmänna villkor"])
        assert "argument --type: invalid choice" in capsys.readouterr().err

    @pytest.mark.parametrize("limit", ["0", "-3", "tre", "2.5"])
    def test_the_limit_of_search_is_a_whole_number_of_at_least_one(
        self, limit: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as stopped:
            build_parser().parse_args(["search", "Hur stort är vitet?", "--limit", limit])

        assert stopped.value.code == 2
        assert (
            f"argument --limit: must be a whole number of at least 1, not {limit!r}"
            in capsys.readouterr().err
        )


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
        "clear_index": record("clear_index"),
        "lock_index": record("lock_index"),
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
            "lock_index",
            "save_sections",
            "save_extraction",
            "save_findings",
            "clear_index",
            "commit",
            "write_report",
        ]
        assert len(list((tmp_path / "data" / "reports").glob("*-inlasning.*"))) == 2

    def test_the_save_empties_the_search_index_and_says_so(
        self, database: FakeDatabase, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A search must not find a chunk a new finding holds back (fail closed); in the
        # same transaction, so a failed save leaves the old index with its old chunks.
        with caplog.at_level(logging.INFO, logger="avtalsagent.ingestion"):
            process(["IT-drift"])

        assert database.events.index("clear_index") < database.events.index("commit")
        assert (
            "Saved the sections, chunks, facts, references and findings in one transaction, "
            "and emptied the search index: build it again with `index`"
        ) in caplog.messages

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


# --- index: step 6 ----------------------------------------------------------------------------

TERMS = "a" * 64  # IT-drift Mindre: Allmänna villkor
CARD = "c" * 64  # IT-drift Mindre: Advania's card, held back in these tests
HEADER = (
    "IT-drift (23.3-5890-2023) › Allmänna villkor › 6.21.4 Ansvar och vite vid övriga avtalsbrott"
)
PENALTY = [
    "6.21.4 Ansvar och vite vid övriga avtalsbrott",
    "Vitets storlek uppgår till 25 000 SEK och utgår per påbörjad vecka som bristen kvarstår.",
    "Vite ska högst uppgå till 100 000 SEK.",
    "Om bristen inte åtgärdas utgår maximalt vite.",
]


def index_sources(texts: Sequence[str] = PENALTY) -> list[IndexSource]:
    """The penalty clause in one chunk per sentence, and one chunk of the held card."""
    section = "\n\n".join(texts)
    return [
        IndexSource(ChunkKey(TERMS, 40, position), HEADER, text, section, "6.21.4")
        for position, text in enumerate(texts)
    ] + [IndexSource(ChunkKey(CARD, 3, 0), HEADER, "Ramavtal", "Ramavtal", None)]


SCOPES = {
    sha256: DocumentScope(sha256, ("IT-drift",), ("23.3-5890-2023",), (), ("IT-drift Mindre",))
    for sha256 in (TERMS, CARD)
}


class FakeEmbedder:
    """Gives every text the same unit vector and records each request's texts."""

    name = "text-embedding-3-large:4"

    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.requests.append(list(texts))
        return [[0.5, 0.5, 0.5, 0.5] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@pytest.fixture
def index_database(
    monkeypatch: pytest.MonkeyPatch, database: FakeDatabase
) -> tuple[FakeDatabase, dict[str, Any]]:
    """`index` on the penalty clause with the card held back; the first chunk is cached."""
    saved: dict[str, Any] = {}
    settings = Settings(_env_file=None, openai_api_key=None, embedding_batch_size=2)

    def cached(session: object, model: str, hashes: set[str]) -> dict[str, list[float]]:
        database.events.append("cached_embeddings")
        first = text_hash(f"{HEADER}\n\n{PENALTY[0]}")
        return {first: [0.5, 0.5, 0.5, 0.5]} if first in hashes else {}

    def save_cache(session: object, model: str, embeddings: dict[str, list[float]]) -> None:
        database.events.append("save_cached_embeddings")
        saved.setdefault("cache", []).append((model, embeddings))

    def save(session: object, plan: IndexPlan, terms: TermIndex, *rest: Any) -> None:
        database.events.append("save_index")
        saved["index"] = (plan, terms, *rest)

    replaced: dict[str, Any] = {
        "get_settings": lambda: settings,
        "index_sources": lambda session: index_sources(),
        "quarantine": lambda session: Quarantine(frozenset({CARD}), frozenset()),
        "catalog_links": lambda session: [],
        "register_entries": lambda session: [],
        "metadata_agreement_numbers": lambda session: {},
        "metadata_document_types": lambda session: {},
        "files_gone_since_checked": lambda session: set(),
        "document_scopes": lambda links, register, numbers, types: SCOPES,
        "cached_embeddings": cached,
        "save_cached_embeddings": save_cache,
        "save_index": save,
    }
    for name, value in replaced.items():
        monkeypatch.setattr(cli, name, value)
    return database, saved


class TestIndex:
    def test_only_texts_missing_from_the_cache_are_embedded_and_each_batch_is_cached(
        self, index_database: tuple[FakeDatabase, dict[str, Any]]
    ) -> None:
        database, saved = index_database
        embedder = FakeEmbedder()

        index(embedder)

        texts = [f"{HEADER}\n\n{text}" for text in PENALTY]
        # The first text is cached; the other three go two to a request (EMBEDDING_BATCH_SIZE).
        assert embedder.requests == [texts[1:3], texts[3:]]
        assert [list(batch) for _, batch in saved["cache"]] == [
            [text_hash(text) for text in texts[1:3]],
            [text_hash(text) for text in texts[3:]],
        ]
        # Each batch is committed on its own, so a failed run keeps what it has paid for.
        assert database.events == [
            "cached_embeddings",
            "save_cached_embeddings",
            "commit",
            "save_cached_embeddings",
            "commit",
            "lock_index",
            "save_index",
            "commit",
        ]

    def test_the_index_is_saved_with_every_embedding_the_terms_and_the_build(
        self, index_database: tuple[FakeDatabase, dict[str, Any]]
    ) -> None:
        _, saved = index_database

        index(FakeEmbedder())

        plan, terms, embeddings, scopes, model, analyser = saved["index"]
        assert [chunk.key for chunk in plan.chunks] == [ChunkKey(TERMS, 40, n) for n in range(4)]
        assert plan.held_back == 1  # the card
        assert terms.chunk_total == 4
        assert set(embeddings) == {chunk.text_hash for chunk in plan.chunks}
        assert scopes == SCOPES
        assert (model, analyser) == ("text-embedding-3-large:4", swedish_text.ANALYSER)

    def test_nothing_is_saved_when_the_chunks_changed_while_embedding(
        self, index_database: tuple[FakeDatabase, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A `process` run between the first read and the save: its chunks would be indexed
        # with the old ones' embeddings and quarantine.
        database, saved = index_database
        reads = iter([index_sources(), index_sources(PENALTY[:2])])
        monkeypatch.setattr(cli, "index_sources", lambda session: next(reads))

        with pytest.raises(CommandError, match="changed while the texts were embedded"):
            index(FakeEmbedder())

        assert "save_index" not in database.events
        assert len(saved["cache"]) == 2  # the embeddings are kept for the next run

    def test_files_no_page_links_to_stop_the_index_before_embedding(
        self, index_database: tuple[FakeDatabase, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A `fetch` since `process` can unlink a file; its chunks would have no scope.
        monkeypatch.setattr(
            cli, "document_scopes", lambda links, register, numbers, types: {CARD: SCOPES[CARD]}
        )
        embedder = FakeEmbedder()

        with pytest.raises(CommandError, match=r"1 files with chunks have no link.*aaaaaaaaaaaa"):
            index(embedder)

        assert embedder.requests == []

    def test_files_whose_pages_went_after_process_stop_the_index_before_embedding(
        self, index_database: tuple[FakeDatabase, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Step 5 holds back a file whose pages are all gone, but only once `process` runs.
        monkeypatch.setattr(cli, "files_gone_since_checked", lambda session: {TERMS, CARD})
        embedder = FakeEmbedder()

        with pytest.raises(CommandError, match=r"1 files lost their last agreement page.*process"):
            index(embedder)

        assert embedder.requests == []

    def test_pages_that_go_while_it_embeds_stop_the_save(
        self, index_database: tuple[FakeDatabase, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[None] = []

        def gone(session: object) -> set[str]:
            calls.append(None)
            return set() if len(calls) == 1 else {TERMS}

        monkeypatch.setattr(cli, "files_gone_since_checked", gone)
        database, saved = index_database

        with pytest.raises(CommandError, match="changed while the texts were embedded"):
            index(FakeEmbedder())

        assert "index" not in saved
        assert database.events[-1] == "lock_index"

    def test_step_6_logs_what_it_indexed_and_where_the_embeddings_came_from(
        self,
        index_database: tuple[FakeDatabase, dict[str, Any]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="avtalsagent.ingestion"):
            index(FakeEmbedder())

        terms: TermIndex = index_database[1]["index"][1]
        assert caplog.messages[0] == (
            "Step 6: 4 chunks to index, 4 texts: 1 from the cache, 3 to embed with "
            "text-embedding-3-large:4"
        )
        assert caplog.messages[-1].startswith(
            f"Step 6: 4 chunks of 1 files indexed, 1 held back by the quarantine; "
            f"{len(terms.ids)} words; "
            "texts from the cache 1, embedded 3; "
        )


# --- The commands that need OPENAI_API_KEY ----------------------------------------------------


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """main() with settings without an OpenAI key; returns the steps that ran."""
    ran: list[str] = []
    settings = Settings(_env_file=None, openai_api_key=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    for step in ("fetch", "parse", "process", "index", "search"):
        monkeypatch.setattr(cli, step, lambda *args, step=step: ran.append(step))
    return ran


@pytest.mark.parametrize(
    "argv", [["index"], ["run", "--area", "IT-drift"], ["search", "Hur stort är vitet?"]]
)
def test_a_command_that_embeds_stops_without_an_openai_key(
    argv: list[str],
    no_key: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("sys.argv", ["avtalsagent.ingestion", *argv])

    with (
        caplog.at_level(logging.ERROR, logger="avtalsagent.ingestion"),
        pytest.raises(SystemExit) as stopped,
    ):
        cli.main()

    assert stopped.value.code == 1
    assert caplog.messages == [
        f"{argv[0]}: OPENAI_API_KEY is not set: the search index needs it to embed the chunks "
        "and the questions"
    ]
    assert no_key == []  # `run` stops before it fetches anything


def test_run_does_steps_1_to_6_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def step(name: str, result: Any = None) -> Any:
        def run_step(*args: Any) -> Any:
            ran.append(name)
            return result

        return run_step

    monkeypatch.setattr(cli, "fetch", step("fetch", Counter()))
    monkeypatch.setattr(cli, "parse", step("parse", Counter()))
    monkeypatch.setattr(cli, "process", step("process"))
    monkeypatch.setattr(cli, "index", step("index"))

    cli.run(["IT-drift"], FakeEmbedder())

    assert ran == ["fetch", "parse", "process", "index"]


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        (
            ["search", "Hur stort är vitet?"],
            IndexNotReadyError(
                "there is no search index: build it with `python -m avtalsagent.ingestion index`"
            ),
        ),
        (
            ["index"],
            EmbeddingError(
                "the embedding model text-embedding-3-large:1536 could not be reached "
                "(AuthenticationError). Fix the key or the connection and run again."
            ),
        ),
    ],
)
def test_an_index_or_embedding_error_stops_the_command_with_its_message(
    argv: list[str],
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(*args: Any) -> None:
        raise error

    monkeypatch.setattr(cli, argv[0], fail)
    monkeypatch.setattr(cli, "require_embedder", lambda settings: FakeEmbedder())
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr("sys.argv", ["avtalsagent.ingestion", *argv])

    with (
        caplog.at_level(logging.ERROR, logger="avtalsagent.ingestion"),
        pytest.raises(SystemExit) as stopped,
    ):
        cli.main()

    assert stopped.value.code == 1
    assert caplog.messages == [f"{argv[0]}: {error}"]


def test_search_passes_the_filters_and_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(cli, "search", lambda *args: calls.append(args))
    monkeypatch.setattr(cli, "require_embedder", lambda settings: "the embedder")
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "avtalsagent.ingestion",
            "search",
            "Vite?",
            "--agreement",
            "23.3-5890-2023-003",
            "--type",
            "supplier_agreement",
        ],
    )

    cli.main()

    assert calls == [
        (
            "Vite?",
            SearchFilters(
                agreement_number="23.3-5890-2023-003", document_type="supplier_agreement"
            ),
            None,
            "the embedder",
        )
    ]


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_a_blank_question_stops_search_with_one_line(
    question: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_database() -> None:
        raise AssertionError("a blank question must not reach the database")

    monkeypatch.setattr(cli, "create_db_engine", no_database)
    monkeypatch.setattr(cli, "require_embedder", lambda settings: FakeEmbedder())
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr("sys.argv", ["avtalsagent.ingestion", "search", question])

    with (
        caplog.at_level(logging.ERROR, logger="avtalsagent.ingestion"),
        pytest.raises(SystemExit) as stopped,
    ):
        cli.main()

    assert stopped.value.code == 1
    assert caplog.messages == [
        'search: the question is blank: ask one, e.g. search "Hur stort är vitet?"'
    ]


def test_search_refuses_a_blank_question_before_it_opens_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_database() -> None:
        raise AssertionError("a blank question must not reach the database")

    monkeypatch.setattr(cli, "create_db_engine", no_database)

    with pytest.raises(CommandError, match="the question is blank"):
        cli.search("  ", SearchFilters(), None, FakeEmbedder())


# --- search: the printed hits -----------------------------------------------------------------


def hit(**changes: Any) -> SectionHit:
    values: dict[str, Any] = {
        "sha256": TERMS,
        "section_position": 40,
        "section_number": "6.21.4",
        "section_title": "Ansvar och vite vid övriga avtalsbrott",
        "path": ("6 Allmänna villkor", "6.21 Avtalsbrott och påföljder"),
        "page_start": 24,
        "page_end": 25,
        "file_title": "Allmänna villkor",
        "document_type": "general_terms",
        "framework_areas": ("IT-drift",),
        "page_titles": ("IT-drift Mindre, upp till 200 anställda",),
        "agreement_numbers": (),
        "snippet": "\n\n".join(PENALTY[1:]),
        "score": 1 / 61 + 1 / 63,
        "vector_rank": 1,
        "text_rank": 3,
        "copies": (SectionCopy("b" * 64, 12, "6.21.4", "Upphandlingsdokument"),),
    }
    return SectionHit(**(values | changes))


class TestHitLines:
    def test_a_hit_shows_where_it_is_its_best_chunk_and_its_copies(self) -> None:
        assert hit_lines([hit()]) == [
            "1. 6.21.4 Ansvar och vite vid övriga avtalsbrott",
            "   score 0.0323 (vector 1, BM25 3)",
            "   Allmänna villkor (general_terms), p. 24-25, aaaaaaaaaaaa section 40",
            "   IT-drift: IT-drift Mindre, upp till 200 anställda",
            "   " + " ".join(PENALTY[1:]),
            "   same text: Upphandlingsdokument 6.21.4, bbbbbbbbbbbb section 12",
        ]

    def test_a_card_shows_its_agreement_and_a_branch_without_the_hit_is_left_out(self) -> None:
        card = hit(
            section_number=None,
            section_title="Text före första rubriken",
            page_start=None,
            page_end=None,
            agreement_numbers=("23.3-5890-2023-003",),
            text_rank=None,
            copies=(),
        )

        lines = hit_lines([card])

        assert lines[:4] == [
            "1. Text före första rubriken",
            "   score 0.0323 (vector 1)",
            "   Allmänna villkor (general_terms), aaaaaaaaaaaa section 40",
            "   IT-drift: IT-drift Mindre, upp till 200 anställda",
        ]
        assert lines[4] == "   agreement 23.3-5890-2023-003"

    def test_a_long_chunk_is_cut(self) -> None:
        [line] = [line for line in hit_lines([hit(snippet="vite " * 100)]) if "vite vite" in line]

        assert line.endswith(" ...")
        assert len(line) <= len("   ") + 300 + len(" ...")

    def test_no_hits_say_so(self) -> None:
        assert hit_lines([]) == ["No sections found."]
