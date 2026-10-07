"""Tests for evals.run_retrieval_eval: scores, summaries, reports and the command line.

The rankings are made up. Section hashes are short readable names
("h-vite"), which the metrics treat as any other key. The runs against the
database are tested in tests/integration/test_retrieval_eval.py; here they
are replaced, so the command line's options, errors and output are tested
without one.
"""

import dataclasses
import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from avtalsagent.config import Settings
from avtalsagent.domain.search import ChunkKey, RankedChunk, SearchFilters
from avtalsagent.ingestion.__main__ import CommandError
from avtalsagent.ingestion.index_store import IndexBuildInfo
from avtalsagent.retrieval.embedder import EmbeddingError
from avtalsagent.retrieval.hybrid_search import IndexNotReadyError
from evals import run_retrieval_eval as runner
from evals.gold import (
    GoldFile,
    GoldQuestion,
    GoldScope,
    RetrievalQuestion,
    SkippedQuestion,
    SkipReason,
)
from evals.run_retrieval_eval import (
    METRICS,
    Comparison,
    EvalReport,
    QuestionResult,
    build_parser,
    by_category,
    compare,
    evaluate_question,
    mean_scores,
    overall_lines,
    render_markdown,
    report_json,
    report_stem,
    run_settings,
    scores,
    source_ranks,
    system_rankings,
    write_reports,
)

TERMS = "a" * 64
CARD = "c" * 64
QUESTION_TEXT = "Hur stort är vitet om leveransen blir försenad?"


def chunk(sha256: str, section: int, position: int, section_hash: str) -> RankedChunk:
    return RankedChunk(ChunkKey(sha256, section, position), section_hash)


# The vector branch finds two chunks of "h-vite" and then "h-uppsagning"; BM25 finds
# "h-uppsagning" first and then "h-priser".
VECTOR = [
    chunk(TERMS, 1, 0, "h-vite"),
    chunk(TERMS, 1, 1, "h-vite"),
    chunk(TERMS, 2, 0, "h-uppsagning"),
]
TEXT = [chunk(TERMS, 2, 0, "h-uppsagning"), chunk(CARD, 0, 0, "h-priser")]


def gold_question(question_id: str = "t01", category: str = "enkel uppslagning") -> GoldQuestion:
    return GoldQuestion(
        id=question_id,
        category=category,
        area="Exempelområdet",
        scope=GoldScope(framework_area="Exempelområdet"),
        question=QUESTION_TEXT,
        answer="5 000 kronor per påbörjad vecka.",
        answerable=True,
        sources=(),
        why_hard="Frågan säger försenad, avtalet säger förseningen.",
        difficulty=1,
    )


def retrieval_question(
    *required: set[str], question_id: str = "t01", category: str = "enkel uppslagning"
) -> RetrievalQuestion:
    return RetrievalQuestion(
        gold_question(question_id, category),
        tuple(frozenset(source) for source in required),
        SearchFilters(framework_area="Exempelområdet"),
    )


def result(
    question_id: str, category: str, ndcg: dict[str, float], required: int = 1
) -> QuestionResult:
    """A result with the given nDCG@10 per system and every other score 0."""
    systems = {
        system: runner.SystemResult(
            ranking=("h-vite",),
            source_ranks=(1,) * required if value else (None,) * required,
            scores={metric: 0.0 for metric in METRICS} | {"ndcg@10": value},
        )
        for system, value in ndcg.items()
    }
    return QuestionResult(
        id=question_id,
        category=category,
        filters=SearchFilters(),
        required=tuple(frozenset({f"h-{question_id}-{n}"}) for n in range(required)),
        unindexed_sources=0,
        systems=systems,
    )


BUILD = IndexBuildInfo(
    embedding_model="text-embedding-3-large:1536",
    analyser="sv-1 snowballstemmer 3.1.1",
    term_count=21_480,
    chunk_count=13_175,
    held_back_count=212,
    document_count=207,
    built_at=datetime(2026, 10, 7, 9, 30, tzinfo=UTC),
)


def report(
    results: list[QuestionResult] | None = None, offline: bool = False, **changes: Any
) -> EvalReport:
    values: dict[str, Any] = {
        "offline": offline,
        "created_at": datetime(2026, 10, 7, 10, 0, 5, tzinfo=UTC),
        "gold_path": "evals/datasets/gold_sv.jsonl",
        "gold_sha256": "f" * 64,
        "gold_questions": 5,
        "index": BUILD,
        "candidates": 100,
        "rrf_k": 60,
        "results": tuple(
            results
            or [
                result("q01", "enkel uppslagning", {"vector": 0.5, "bm25": 0.0, "hybrid": 1.0}),
                result("q02", "enkel uppslagning", {"vector": 1.0, "bm25": 0.5, "hybrid": 1.0}),
                result("q14", "flerstegshänvisning", {"vector": 0.0, "bm25": 1.0, "hybrid": 0.5}),
            ]
        ),
        "skipped": (
            SkippedQuestion("q10", "registerfråga", SkipReason.REGISTER_ONLY),
            SkippedQuestion("q27", "fråga utan svar i avtalen", SkipReason.NO_ANSWER),
        ),
    }
    return EvalReport(**(values | changes))


# --- Rankings and scores ----------------------------------------------------------------------


def test_each_system_ranks_section_groups() -> None:
    rankings = system_rankings(VECTOR, TEXT, rrf_k=60)

    assert rankings["vector"] == ["h-vite", "h-uppsagning"]  # one place for both chunks
    assert rankings["bm25"] == ["h-uppsagning", "h-priser"]
    # In both branches: 1/62 + 1/61; first in one: 1/61; second in one: 1/62.
    assert rankings["hybrid"] == ["h-uppsagning", "h-vite", "h-priser"]


def test_the_scores_of_one_ranking() -> None:
    required = [frozenset({"h-vite"}), frozenset({"h-priser", "h-x"})]

    values = scores(["h-uppsagning", "h-vite", "h-priser"], required)

    assert values["recall@1"] == 0.0
    assert values["recall@3"] == values["recall@50"] == 1.0
    assert values["mrr@10"] == 0.5
    ideal = 1 + 1 / math.log2(3)
    assert values["ndcg@10"] == pytest.approx((1 / math.log2(3) + 1 / math.log2(4)) / ideal)
    assert values["all_found@10"] == 1.0
    assert set(values) == set(METRICS)


def test_first_ranks_are_taken_over_the_whole_ranking() -> None:
    ranking = [f"h-{n}" for n in range(60)]

    ranks = source_ranks(ranking, [frozenset({"h-54", "h-58"}), frozenset({"h-saknas"})])

    assert ranks == (55, None)
    assert scores(ranking, [frozenset({"h-54"})])["recall@50"] == 0.0


def test_a_question_is_scored_for_every_system() -> None:
    question = retrieval_question({"h-vite"}, {"h-priser"}, {"h-utanfor"})
    indexed = {"h-vite", "h-uppsagning", "h-priser"}

    outcome = evaluate_question(question, VECTOR, TEXT, 60, indexed)

    assert (outcome.id, outcome.category) == ("t01", "enkel uppslagning")
    assert outcome.filters == SearchFilters(framework_area="Exempelområdet")
    assert outcome.unindexed_sources == 1  # no search can find "h-utanfor"
    vector, bm25, hybrid = (outcome.systems[system] for system in runner.SYSTEMS)
    assert vector.ranking == ("h-vite", "h-uppsagning")
    assert vector.source_ranks == (1, None, None)
    assert vector.first_rank == 1
    assert bm25.source_ranks == (None, 2, None)
    assert bm25.first_rank == 2
    assert hybrid.source_ranks == (2, 3, None)
    assert hybrid.scores["recall@3"] == pytest.approx(2 / 3)
    assert hybrid.scores["all_found@10"] == 0.0


def test_a_question_found_by_no_system_has_no_first_rank() -> None:
    outcome = evaluate_question(retrieval_question({"h-annat"}), VECTOR, [], 60, {"h-annat"})

    assert outcome.systems["bm25"].ranking == ()
    assert {system.first_rank for system in outcome.systems.values()} == {None}
    assert outcome.unindexed_sources == 0


# --- Summaries --------------------------------------------------------------------------------


def test_means_over_all_questions_and_per_category() -> None:
    results = list(report().results)

    assert mean_scores(results, "hybrid")["ndcg@10"] == pytest.approx(2.5 / 3)
    groups = by_category(results)
    assert list(groups) == ["enkel uppslagning", "flerstegshänvisning"]
    assert mean_scores(groups["enkel uppslagning"], "vector")["ndcg@10"] == 0.75
    assert mean_scores(groups["flerstegshänvisning"], "bm25")["ndcg@10"] == 1.0


def test_the_bootstrap_is_paired_and_seeded() -> None:
    results = list(report().results)

    first = compare(results, "vector", "hybrid")

    assert first == compare(results, "vector", "hybrid")
    assert (first.baseline, first.system, first.metric) == ("vector", "hybrid", "ndcg@10")
    assert first.mean == pytest.approx((0.5 + 0.0 + 0.5) / 3)
    assert first.low <= first.mean <= first.high


def test_a_difference_on_every_question_has_an_interval_without_zero() -> None:
    results = [
        result(f"q{n}", "enkel uppslagning", {"vector": 0.0, "bm25": 0.5, "hybrid": 1.0})
        for n in range(5)
    ]

    assert compare(results, "vector", "hybrid") == Comparison(
        "vector", "hybrid", "ndcg@10", 1.0, 1.0, 1.0
    )


# --- Reports ----------------------------------------------------------------------------------


def test_the_report_is_named_after_the_model_and_the_mode() -> None:
    assert report_stem(report()) == "retrieval-text-embedding-3-large-1536"
    assert report_stem(report(offline=True)) == "retrieval-text-embedding-3-large-1536-offline"


def test_the_json_report_holds_the_numbers_rankings_and_index() -> None:
    long_ranking = tuple(f"h-{n}" for n in range(80))
    measured = evaluate_question(retrieval_question({"h-vite"}), VECTOR, TEXT, 60, {"h-vite"})
    deep = QuestionResult(
        "q03",
        "enkel uppslagning",
        SearchFilters(agreement_number="23.3-0000-2024-002"),
        (frozenset({"h-79", "h-1"}),),
        0,
        {
            system: runner.SystemResult(
                long_ranking, (2,), scores(long_ranking, [frozenset({"h-1"})])
            )
            for system in runner.SYSTEMS
        },
    )

    data = json.loads(report_json(report([measured, deep])))

    assert data["mode"] == "database"
    assert data["gold"] == {
        "path": "evals/datasets/gold_sv.jsonl",
        "sha256": "f" * 64,
        "questions": 5,
        "evaluated": 2,
        "skipped": [
            {"id": "q10", "category": "registerfråga", "reason": "register_only"},
            {"id": "q27", "category": "fråga utan svar i avtalen", "reason": "no_answer"},
        ],
    }
    assert data["index"] == {
        "embedding_model": "text-embedding-3-large:1536",
        "analyser": "sv-1 snowballstemmer 3.1.1",
        "chunk_count": 13_175,
        "held_back_count": 212,
        "document_count": 207,
        "term_count": 21_480,
        "built_at": "2026-10-07T09:30:00+00:00",
    }
    assert data["parameters"]["candidates"] == 100
    assert data["parameters"]["rrf_k"] == 60
    assert data["parameters"]["bootstrap"] == {"metric": "ndcg@10", "resamples": 10_000, "seed": 0}
    assert set(data["overall"]) == {"vector", "bm25", "hybrid"}
    assert set(data["overall"]["hybrid"]) == set(METRICS)
    assert [item["category"] for item in data["by_category"]] == ["enkel uppslagning"]
    assert [(item["baseline"], item["system"]) for item in data["comparisons"]] == [
        ("vector", "hybrid"),
        ("bm25", "hybrid"),
    ]
    first, second = data["questions"]
    assert first["id"] == "t01"
    assert first["filters"] == {"framework_area": "Exempelområdet", "agreement_number": None}
    assert first["required_sources"] == [["h-vite"]]
    assert first["systems"]["hybrid"]["ranking"] == ["h-uppsagning", "h-vite", "h-priser"]
    assert first["systems"]["hybrid"]["first_rank"] == 2
    assert first["systems"]["bm25"]["first_rank"] is None
    assert second["required_sources"] == [["h-1", "h-79"]]
    assert len(second["systems"]["vector"]["ranking"]) == 50  # the first 50 of 80
    # Ids, hashes and numbers only: no question or document text.
    assert QUESTION_TEXT not in report_json(report([measured, deep]))


def test_the_markdown_report_is_swedish_and_has_every_table() -> None:
    text = render_markdown(report())

    assert text.startswith("# Mätning av sökningen: text-embedding-3-large:1536\n")
    for heading in (
        "## Sammanfattning",
        "## Körning",
        "## Vad som mättes och hur",
        "## Alla frågor",
        "## Hybrid jämförd med grenarna",
        "## Per kategori",
        "## Per fråga",
        "## Frågor som inte mättes",
    ):
        assert f"\n{heading}\n" in text
    assert "- 3 av 5 frågor mättes; 2 hoppades över (se sist)." in text
    assert "- nDCG@10: vektor 0,500, BM25 0,500, hybrid 0,833." in text
    assert "| 3 | hybrid | " in text
    assert "| flerstegshänvisning | 1 | BM25 | " in text
    assert "| q14 | flerstegshänvisning | 1 | – | 1 | 1 | 0,500 |" in text
    assert "| q10 | registerfråga | svaret står bara i registret |" in text
    assert "| q27 | fråga utan svar i avtalen | avtalen besvarar inte frågan |" in text
    assert "13\u00a0175 chunkar ur 207 filer, 21\u00a0480 ord; 212 chunkar hålls" in text
    assert "- **Tid:** 2026-10-07 10:00:05 (UTC)" in text
    assert "Alla källors avsnitt finns i indexet." in text
    assert "Offline" not in text


def test_the_markdown_report_says_how_the_comparison_came_out() -> None:
    results = [
        result(f"q{n:02}", "enkel uppslagning", {"vector": 0.0, "bm25": 1.0, "hybrid": 1.0})
        for n in range(4)
    ]

    text = render_markdown(report(results))

    assert "| hybrid − vektor | +1,000 | +1,000 till +1,000 | bättre, större än slumpen |" in text
    assert "| hybrid − BM25 | +0,000 | +0,000 till +0,000 | ingen skillnad |" in text


def test_the_markdown_report_of_an_offline_run_says_so() -> None:
    unindexed = dataclasses.replace(
        result("q05", "enkel uppslagning", {system: 0.0 for system in runner.SYSTEMS}),
        unindexed_sources=1,
    )

    text = render_markdown(report([unindexed], offline=True, skipped=()))

    assert text.startswith("# Mätning av sökningen: text-embedding-3-large:1536 (offline)\n")
    assert "index i minnet, byggt av de lagrade chunkarna (offline)" in text
    assert "- Offline byggs indexet i minnet" in text
    assert "- 1 källa finns inte i indexet" in text
    assert "per fråga: q05 (1)." in text
    assert text.endswith("## Frågor som inte mättes\n\nAlla frågor mättes.\n")


def test_the_printed_table_has_one_line_per_system() -> None:
    lines = overall_lines(report())

    assert lines[0] == (
        "Retrieval evaluation, database index, text-embedding-3-large:1536: 3 questions, 2 skipped"
    )
    assert lines[1].split() == [
        "system",
        "R@1",
        "R@3",
        "R@5",
        "R@10",
        "R@20",
        "R@50",
        "MRR@10",
        "nDCG@10",
        "all@10",
    ]
    assert lines[4].split()[0] == "hybrid"
    assert lines[4].split()[8] == "0.833"
    assert lines[5].startswith("ndcg@10 hybrid - vector: +0.333 (95% interval ")


def test_the_reports_are_written_to_the_out_directory(tmp_path: Path) -> None:
    markdown, data = write_reports(report(), tmp_path / "rapporter")

    assert markdown == tmp_path / "rapporter" / "retrieval-text-embedding-3-large-1536.md"
    assert data == tmp_path / "rapporter" / "retrieval-text-embedding-3-large-1536.json"
    assert markdown.read_text(encoding="utf-8") == render_markdown(report())
    assert json.loads(data.read_text(encoding="utf-8"))["evaluation"] == "retrieval"


def test_the_gold_path_in_the_reports_names_no_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert runner._shown_path(Path("evals/datasets/gold_sv.jsonl")) == (
        "evals/datasets/gold_sv.jsonl"
    )
    assert runner._shown_path(tmp_path / "evals" / "guld.jsonl") == "evals/guld.jsonl"
    assert runner._shown_path(Path("/var/tmp/annan/guld.jsonl")) == "guld.jsonl"


def test_reports_that_cannot_be_written_are_a_command_error(tmp_path: Path) -> None:
    taken = tmp_path / "fil"
    taken.write_text("")

    with pytest.raises(CommandError, match="cannot write the reports to"):
        write_reports(report(), taken)


# --- The command line -------------------------------------------------------------------------


def test_the_options_default_to_the_settings() -> None:
    args = build_parser().parse_args([])

    assert args.gold == Path("evals/datasets/gold_sv.jsonl")
    assert args.out == Path("evals/reports")
    assert (args.candidates, args.rrf_k, args.offline, args.model, args.dimensions) == (
        None,
        None,
        False,
        None,
        None,
    )


@pytest.mark.parametrize(
    "argv", [["--candidates", "0"], ["--rrf-k", "-1"], ["--dimensions", "många"]]
)
def test_counts_must_be_whole_numbers_of_at_least_one(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_run_settings_override_the_embedding_model() -> None:
    settings = Settings(_env_file=None)

    changed = run_settings(settings, "text-embedding-3-small", 512)

    assert (changed.embedding_model, changed.embedding_dimensions) == (
        "text-embedding-3-small",
        512,
    )
    assert run_settings(settings, None, None) == settings


SECRET = "sk-test-0123456789abcdef"


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """main() with settings of a test key, no database, and runs that record their arguments."""
    calls: dict[str, Any] = {}
    settings = Settings(_env_file=None, openai_api_key=SECRET, search_candidates=80, rrf_k=40)
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(runner, "configure_logging", lambda level: None)
    monkeypatch.setattr(runner, "create_db_engine", lambda: "engine")
    monkeypatch.setattr(runner, "session_factory", lambda engine: f"factory of {engine}")

    def database_run(
        factory: str, embedder: Any, gold: GoldFile, candidates: int, rrf_k: int
    ) -> EvalReport:
        calls["database"] = (factory, embedder.name, gold.path, candidates, rrf_k)
        return report()

    def offline_run(
        factory: str, embedder: Any, gold: GoldFile, candidates: int, rrf_k: int, batch: int
    ) -> EvalReport:
        calls["offline"] = (embedder.name, candidates, rrf_k, batch)
        return report(offline=True, index=BUILD)

    monkeypatch.setattr(runner, "database_run", database_run)
    monkeypatch.setattr(runner, "offline_run", offline_run)
    gold = tmp_path / "guld.jsonl"
    line = {
        "id": "t01",
        "category": "enkel uppslagning",
        "area": "Exempelområdet",
        "scope": {},
        "question": QUESTION_TEXT,
        "answer": "5 000 kronor.",
        "answerable": False,
        "sources": [],
        "why_hard": "",
        "difficulty": 1,
    }
    gold.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
    calls["gold"] = gold
    calls["out"] = tmp_path / "rapporter"
    return calls


def test_main_measures_the_database_index_and_prints_the_table(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    runner.main(["--gold", str(cli["gold"]), "--out", str(cli["out"])])

    assert cli["database"] == (
        "factory of engine",
        "text-embedding-3-large:1536",
        cli["gold"],
        80,
        40,
    )
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith("Retrieval evaluation, database index")
    assert printed[-1] == (
        f"Reports: {cli['out'] / 'retrieval-text-embedding-3-large-1536.md'} and "
        f"{cli['out'] / 'retrieval-text-embedding-3-large-1536.json'}"
    )
    assert (cli["out"] / "retrieval-text-embedding-3-large-1536.json").is_file()


def test_main_offline_takes_another_model_and_the_options(cli: dict[str, Any]) -> None:
    runner.main(
        [
            "--gold",
            str(cli["gold"]),
            "--out",
            str(cli["out"]),
            "--offline",
            "--model",
            "text-embedding-3-small",
            "--dimensions",
            "512",
            "--candidates",
            "50",
            "--rrf-k",
            "10",
        ]
    )

    assert cli["offline"] == ("text-embedding-3-small:512", 50, 10, 100)
    assert "database" not in cli
    assert (cli["out"] / "retrieval-text-embedding-3-large-1536-offline.md").is_file()


def test_another_model_needs_offline(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        runner.main(["--model", "text-embedding-3-small"])

    assert stopped.value.code == 2
    assert "--model and --dimensions need --offline" in capsys.readouterr().err


def run_failing(
    argv: list[str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int | str | None, list[str], str]:
    """main() that is expected to stop: its exit code, its log messages, everything printed."""
    with (
        caplog.at_level(logging.ERROR, logger="evals.retrieval"),
        pytest.raises(SystemExit) as stopped,
    ):
        runner.main(argv)
    printed = capsys.readouterr()
    return stopped.value.code, caplog.messages, printed.out + printed.err + caplog.text


def test_a_missing_gold_file_stops_with_one_line(
    cli: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    code, messages, _ = run_failing(["--gold", str(tmp_path / "saknas.jsonl")], caplog, capsys)

    assert code == 1
    assert messages == [
        f"retrieval evaluation: cannot read the gold file {tmp_path / 'saknas.jsonl'}: "
        "No such file or directory"
    ]
    assert "database" not in cli


def test_no_openai_key_stops_with_one_line(
    cli: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    no_key = Settings(_env_file=None, openai_api_key=None)
    monkeypatch.setattr(runner, "get_settings", lambda: no_key)

    code, messages, _ = run_failing(["--gold", str(cli["gold"])], caplog, capsys)

    assert code == 1
    assert messages == [
        "retrieval evaluation: OPENAI_API_KEY is not set: the search index needs it to embed "
        "the chunks and the questions"
    ]
    assert "database" not in cli


@pytest.mark.parametrize(
    "error",
    [
        IndexNotReadyError(
            "there is no search index: build it with `python -m avtalsagent.ingestion index`"
        ),
        EmbeddingError(
            "the embedding model text-embedding-3-large:1536 could not be reached "
            "(AuthenticationError). Fix the key or the connection and run again."
        ),
        CommandError("the search index was rebuilt while the questions were embedded"),
    ],
)
def test_an_error_of_the_run_stops_with_one_line_and_never_the_key(
    error: Exception,
    cli: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: Any) -> EvalReport:
        raise error

    monkeypatch.setattr(runner, "database_run", fail)

    code, messages, output = run_failing(["--gold", str(cli["gold"])], caplog, capsys)

    assert code == 1
    assert messages == [f"retrieval evaluation: {error}"]
    assert SECRET not in output


def test_a_database_that_cannot_be_reached_stops_with_one_line_without_its_address(
    cli: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: Any) -> EvalReport:
        refused = ConnectionRefusedError('connection to server at "db.example", port 5432 failed')
        raise OperationalError("SELECT 1", {}, refused)

    monkeypatch.setattr(runner, "database_run", fail)

    code, messages, output = run_failing(["--gold", str(cli["gold"])], caplog, capsys)

    assert code == 1
    assert messages == ["retrieval evaluation: the database cannot be reached; is it running?"]
    assert "db.example" not in output
