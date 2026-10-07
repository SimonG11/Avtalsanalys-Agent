"""Integration test: the retrieval evaluation (evals/run_retrieval_eval.py) against a real Postgres.

What:
    Runs the evaluation on the corpus of `test_index_store.py`, indexed by
    the `index` command with a stand-in embedder, and a small gold file of
    its clauses: two retrieval questions (one with copies of a section as
    alternatives, one with two sources and an agreement filter), a
    register-only question and one the agreements do not answer. Checks
    the database mode end to end (the gold resolved through the stored
    sections, the three rankings, the skipped questions, the reports), that
    the offline mode ranks exactly as the database mode, that it embeds and
    caches only what the cache lacks, that a source whose section is not in
    the index is counted, and that a gold source that does not match the
    sections, a gold file with nothing to measure, an index of another model
    or no index stops the run before any embedding.

Why:
    The evaluation's numbers are only worth something if they are the
    search's: the database mode must call the search's own queries on the
    built index, and the offline mode, meant for comparing embedding models,
    must rank as they do. The resolution of the gold reads `document_section`,
    which unit tests cannot.

How:
    Uses the `engine` fixture from conftest.py and the corpus, embedders and
    `build_index` of `test_index_store.py`. `TopicEmbedder` gives readable
    rankings; `SignEmbedder` varied ones with exact float32 scores, so the
    database and the offline index rank alike to the bit. The command line is
    run with the database engine and the embedder replaced.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select

from avtalsagent.config import Settings
from avtalsagent.db import models
from avtalsagent.db.session import session_factory
from avtalsagent.ingestion.index_store import cached_embeddings, clear_index
from avtalsagent.ingestion.step6_index import text_hash
from avtalsagent.retrieval.hybrid_search import IndexNotReadyError
from evals import run_retrieval_eval as runner
from evals.gold import GoldError, SkippedQuestion, SkipReason, load_gold
from evals.run_retrieval_eval import EvalReport, database_run, offline_run
from tests.integration.test_index_store import (
    ADVANIA,
    ADVANIA_CARD,
    FACTORING,
    PENALTY,
    PRICE_ADJUSTMENT,
    PROCUREMENT,
    TERMINATION,
    TERMS,
    Clause,
    SignEmbedder,
    TopicEmbedder,
    build_index,
    store_corpus,
)

PENALTY_QUOTE = "Vitets storlek uppgår till 25 000 SEK"
QUOTES = (
    PENALTY_QUOTE,
    "Priserna är fasta i ett (1) år",
    "anlita Underleverantör för factoring eller fakturahantering",
    "har Avropsberättigad rätt att skriftligen säga upp Kontraktet",
)
QUESTIONS = (
    "Hur stort är vitet vid avtalsbrott?",
    "Vad gäller vid prisjustering och factoring?",
    "När får Avropsberättigad säga upp Kontraktet vid fel?",
)


def alternative(
    sha256: str, clause: Clause, quote: str, position: int | None = None
) -> dict[str, Any]:
    heading = clause[0]
    number, _, title = heading.partition(" ")
    values: dict[str, Any] = {
        "sha256": sha256,
        "file_title": "Allmänna villkor",
        "page_title": "IT-drift Mindre, upp till 200 anställda",
        "section_number": number,
        "section_title": title,
        "pages": [clause[2]],
        "quote": quote,
    }
    if position is not None:
        values["section_position"] = position
    return values


def question(
    question_id: str, category: str, text: str, scope: dict[str, str], sources: list[Any]
) -> dict[str, Any]:
    return {
        "id": question_id,
        "category": category,
        "area": "IT-drift",
        "scope": scope,
        "question": text,
        "answer": "Se källorna.",
        "answerable": bool(sources),
        "sources": sources,
        "why_hard": "",
        "difficulty": 1,
    }


def gold_questions(penalty_quote: str = PENALTY_QUOTE) -> list[dict[str, Any]]:
    penalty = {
        "kind": "document",
        "alternatives": [
            alternative(TERMS, PENALTY, penalty_quote),
            # The procurement document's copy of 6.21.4: the same section group.
            alternative(PROCUREMENT, PENALTY, penalty_quote, position=1),
        ],
    }
    price = {
        "kind": "document",
        "alternatives": [alternative(ADVANIA_CARD, PRICE_ADJUSTMENT, QUOTES[1])],
    }
    factoring = {
        "kind": "document",
        "alternatives": [alternative(ADVANIA_CARD, FACTORING, QUOTES[2])],
    }
    register = {"kind": "register", "agreement_numbers": [ADVANIA], "fields": ["org_number"]}
    return [
        question(
            "g01", "enkel uppslagning", QUESTIONS[0], {"framework_area": "IT-drift"}, [penalty]
        ),
        question("g02", "registerfråga", "Vilket organisationsnummer har Advania?", {}, [register]),
        question(
            "g03",
            "flerstegshänvisning",
            QUESTIONS[1],
            {"agreement_number": ADVANIA},
            [price, factoring],
        ),
        question("g04", "fråga utan svar i avtalen", "Vem är rankad etta?", {}, []),
    ]


def write_gold(path: Path, questions: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in questions),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def gold_path(tmp_path: Path) -> Path:
    return write_gold(tmp_path / "guld.jsonl", gold_questions())


class RefusingEmbedder(TopicEmbedder):
    """An embedder of another model, which must not be asked: the check comes first."""

    name = "text-embedding-3-large:1536"

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("a question was embedded before the index was checked")


def first_ranks(report: EvalReport) -> dict[str, dict[str, int | None]]:
    return {
        result.id: {system: outcome.first_rank for system, outcome in result.systems.items()}
        for result in report.results
    }


def test_the_database_mode_measures_the_built_index(engine: Engine, gold_path: Path) -> None:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())

    report = database_run(session_factory(engine), TopicEmbedder(), load_gold(gold_path), 100, 60)

    assert not report.offline
    assert (report.candidates, report.rrf_k, report.gold_questions) == (100, 60, 4)
    assert (report.index.embedding_model, report.index.chunk_count) == ("topics:6", 11)
    assert report.index.held_back_count == 1
    assert report.skipped == (
        SkippedQuestion("g02", "registerfråga", SkipReason.REGISTER_ONLY),
        SkippedQuestion("g04", "fråga utan svar i avtalen", SkipReason.NO_ANSWER),
    )
    penalty, advania = report.results
    # Both alternatives of 6.21.4 are one section group: the one the index stores.
    with session_factory(engine)() as session:
        stored = session.scalars(
            select(models.SearchChunk.section_hash).where(
                models.SearchChunk.sha256.in_([TERMS, PROCUREMENT]),
                models.SearchChunk.section_position == 1,
            )
        ).all()
    penalty_hash = stored[0]
    assert set(stored) == {penalty_hash}
    assert penalty.required == (frozenset({penalty_hash}),)
    assert first_ranks(report)["g01"] == {"vector": 1, "bm25": 1, "hybrid": 1}
    assert penalty.systems["hybrid"].ranking[0] == penalty_hash
    # The agreement filter keeps Advania's card and the procurement's shared files: six
    # section groups, all ranked by the vector branch.
    assert len(advania.systems["vector"].ranking) == 6
    assert advania.systems["vector"].source_ranks[0] == 1  # the one that names prisjuster
    assert advania.systems["hybrid"].scores["all_found@10"] == 1.0
    assert (penalty.unindexed_sources, advania.unindexed_sources) == (0, 0)


def test_the_offline_mode_ranks_as_the_database_and_embeds_nothing_cached(
    engine: Engine, gold_path: Path
) -> None:
    class CountingSigns(SignEmbedder):
        def __init__(self) -> None:
            self.documents = 0

        def embed_documents(self, texts: Any) -> list[list[float]]:
            self.documents += len(texts)
            return super().embed_documents(texts)

    store_corpus(engine)
    build_index(engine, SignEmbedder())
    gold = load_gold(gold_path)
    embedder = CountingSigns()

    in_database = database_run(session_factory(engine), embedder, gold, 3, 60)
    offline = offline_run(session_factory(engine), embedder, gold, 3, 60, 100)

    assert offline.offline
    assert embedder.documents == 0  # every chunk text was in the cache
    assert [result.systems for result in offline.results] == [
        result.systems for result in in_database.results
    ]
    assert offline.index.chunk_count == in_database.index.chunk_count == 11
    assert offline.index.term_count == in_database.index.term_count
    assert offline.index.document_count == 6


def test_the_offline_mode_embeds_and_caches_what_the_cache_lacks(
    engine: Engine, gold_path: Path
) -> None:
    class OtherTopics(TopicEmbedder):
        name = "topics-b:6"

    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    embedder = OtherTopics()

    report = offline_run(session_factory(engine), embedder, load_gold(gold_path), 100, 60, 4)

    assert report.index.embedding_model == "topics-b:6"
    assert len(embedder.texts) == 11  # each indexed chunk's text once, in batches of 4
    with session_factory(engine)() as session:
        cached = cached_embeddings(session, "topics-b:6", {text_hash(t) for t in embedder.texts})
    assert len(cached) == 11
    assert first_ranks(report)["g01"] == {"vector": 1, "bm25": 1, "hybrid": 1}


def test_a_source_whose_section_is_not_in_the_index_is_counted(
    engine: Engine, tmp_path: Path
) -> None:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    # 6.21.2.4 is indexed only from the general terms; the procurement document's copy is
    # held back. Without the general terms' chunk no search can find the section.
    with session_factory(engine).begin() as session:
        session.execute(
            delete(models.SearchChunk).where(
                models.SearchChunk.sha256 == TERMS, models.SearchChunk.section_position == 0
            )
        )
    termination = {
        "kind": "document",
        "alternatives": [alternative(PROCUREMENT, TERMINATION, QUOTES[3], position=0)],
    }
    gold = write_gold(
        tmp_path / "guld.jsonl",
        [question("g05", "enkel uppslagning", QUESTIONS[2], {}, [termination])],
    )

    report = database_run(session_factory(engine), TopicEmbedder(), load_gold(gold), 100, 60)

    [result] = report.results
    assert result.unindexed_sources == 1
    assert {outcome.first_rank for outcome in result.systems.values()} == {None}


def test_a_gold_source_that_does_not_match_the_sections_stops_the_run(
    engine: Engine, tmp_path: Path
) -> None:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    gold = write_gold(
        tmp_path / "guld.jsonl", gold_questions("Vitets storlek uppgår till 30 000 SEK")
    )

    with pytest.raises(
        GoldError,
        match="g01: section 6.21.4 of aaaaaaaaaaaa: the quote is not in the section",
    ):
        database_run(session_factory(engine), TopicEmbedder(), load_gold(gold), 100, 60)


def test_a_gold_file_with_nothing_to_measure_stops_the_run_before_any_embedding(
    engine: Engine, tmp_path: Path
) -> None:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    skipped_only = [item for item in gold_questions() if item["id"] in ("g02", "g04")]
    gold = load_gold(write_gold(tmp_path / "guld.jsonl", skipped_only))
    embedder = RefusingEmbedder()  # another model: the cache has none of its texts

    with pytest.raises(GoldError, match="has no question with a document source to measure"):
        offline_run(session_factory(engine), embedder, gold, 100, 60, 100)
    assert embedder.texts == []


def test_an_index_of_another_model_or_none_stops_the_run_before_any_embedding(
    engine: Engine, gold_path: Path
) -> None:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    gold = load_gold(gold_path)

    with pytest.raises(IndexNotReadyError, match="embedding model topics:6, not text-embedding"):
        database_run(session_factory(engine), RefusingEmbedder(), gold, 100, 60)
    with session_factory(engine).begin() as session:
        clear_index(session)
    with pytest.raises(IndexNotReadyError, match="there is no search index"):
        database_run(session_factory(engine), RefusingEmbedder(), gold, 100, 60)


def test_the_command_writes_both_reports_without_document_text(
    engine: Engine,
    gold_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    monkeypatch.setattr(runner, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(runner, "configure_logging", lambda level: None)
    monkeypatch.setattr(runner, "create_db_engine", lambda: engine)
    monkeypatch.setattr(runner, "require_embedder", lambda settings: TopicEmbedder())
    out = tmp_path / "rapporter"

    runner.main(["--gold", str(gold_path), "--out", str(out)])

    printed = capsys.readouterr().out
    assert printed.startswith(
        "Retrieval evaluation, database index, topics:6: 2 questions, 2 skipped\n"
    )
    markdown = (out / "retrieval-topics-6.md").read_text(encoding="utf-8")
    data = (out / "retrieval-topics-6.json").read_text(encoding="utf-8")
    assert json.loads(data)["index"]["chunk_count"] == 11
    assert "| g04 | fråga utan svar i avtalen | avtalen besvarar inte frågan |" in markdown
    for text in (*QUOTES, *QUESTIONS, "Vitets storlek"):
        assert text not in markdown
        assert text not in data
