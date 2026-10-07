"""Measure the search on the gold questions: vector, BM25 and hybrid, by section.

What:
    `python -m evals.run_retrieval_eval` runs every gold question with a
    document source (evals/datasets/gold_sv.jsonl) through the search's two
    branches and their fusion, and scores the three rankings, vector, bm25
    and hybrid, against the sections the question needs: recall at 1, 3, 5,
    10, 20 and 50, MRR@10, nDCG@10 and whether all sources are in the first
    10, over all questions and per category, with the rank at which each
    source is first found and a paired bootstrap of hybrid against each
    branch on nDCG@10. It writes a JSON report and a Markdown report in
    Swedish to evals/reports/ (`retrieval-<model>-<dimensions>[-offline]`)
    and prints the overall table.

    By default it measures the index built by `index` in PostgreSQL.
    `--offline` builds the index in memory (`evals/offline.py`) from the
    stored chunks instead, with the embeddings of the embedding cache; the
    texts the cache lacks are embedded and cached as `index` does. With
    `--model` or `--dimensions` that measures another embedding model or
    size without touching the production index.

        uv run python -m evals.run_retrieval_eval
        uv run python -m evals.run_retrieval_eval --offline --dimensions 3072

Why:
    ADR 0011 chose hybrid search over either branch alone; this measures
    whether that holds on the pilot's documents and questions, with the
    production code: the database mode calls the search's own branch
    queries and fusion, and the offline index is held to the same rankings
    by an integration test. Comparing an embedding model in the database
    would mean replacing the production index; in memory it costs only the
    embeddings, which the cache keeps for the next run. Register-only and
    unanswerable questions have no section to find; they belong to the
    agent evaluation, and the report lists them as skipped.

How:
    The gold is read and its sources resolved to section hashes through the
    stored sections (`evals/gold.py`) before any embedding call, so a gold
    file that does not match the sections stops the run without cost. The
    database mode first refuses an index built with another embedder or
    analyser, by the search's own check. Each question is embedded with
    `embed_query` and analysed with `query_terms`, as the search does, and
    each branch passes its best `--candidates` chunks (SEARCH_CANDIDATES)
    under the question's filters. The vector and bm25 rankings are each
    branch's section groups (`fusion.section_ranking`), the hybrid one is
    `fusion.fuse` with `--rrf-k` (RRF_K). A source counts as found where any
    of its section hashes is ranked, so a copy of a section counts. First
    ranks are taken over the whole ranking; the JSON keeps the first 50
    hashes of each. Required sources whose sections are not in the index
    (held back by the quarantine, or not stored) are counted, since no
    search can find them.

    The evaluation, the summaries and the rendering are pure functions on
    plain values; `database_run`, `offline_run` and `main` do the I/O. The
    reports hold ids, hashes and numbers only, never a question, a quote or
    a document's text. An error ends the run with one line and exit code 1,
    as in `avtalsagent.ingestion`, and the OpenAI key is never printed or
    logged.
"""

import argparse
import json
import logging
import re
import statistics
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.config import Settings, get_settings
from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.domain.search import RankedChunk, SearchFilters
from avtalsagent.ingestion.__main__ import (
    CommandError,
    configure_logging,
    embed_texts,
    index_input,
    positive_int,
    require_embedder,
)
from avtalsagent.ingestion.extraction_store import register_entries
from avtalsagent.ingestion.index_store import IndexBuildInfo, cached_embeddings, current_build
from avtalsagent.ingestion.step6_index import agreement_procurements
from avtalsagent.retrieval import hybrid_search, swedish_text
from avtalsagent.retrieval.embedder import Embedder, EmbeddingError
from avtalsagent.retrieval.fusion import fuse, section_ranking
from avtalsagent.retrieval.hybrid_search import (
    IndexNotReadyError,
    text_candidates,
    vector_candidates,
)
from evals.gold import (
    DatabaseSections,
    GoldError,
    GoldFile,
    RetrievalQuestion,
    SkippedQuestion,
    SkipReason,
    load_gold,
    retrieval_questions,
)
from evals.metrics import all_hit_at, ndcg_at, paired_bootstrap, recall_at, reciprocal_rank
from evals.offline import OfflineIndex

_log = logging.getLogger("evals.retrieval")

SYSTEMS = ("vector", "bm25", "hybrid")
CUTOFFS = (1, 3, 5, 10, 20, 50)
METRICS = (*(f"recall@{k}" for k in CUTOFFS), "mrr@10", "ndcg@10", "all_found@10")
RANKING_DEPTH = max(CUTOFFS)  # section hashes of each ranking kept in the JSON report
# Hybrid against each branch: (baseline, system), the difference is system - baseline.
COMPARISONS = (("vector", "hybrid"), ("bm25", "hybrid"))
BOOTSTRAP_METRIC = "ndcg@10"
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0

DEFAULT_GOLD = Path("evals/datasets/gold_sv.jsonl")
DEFAULT_OUT = Path("evals/reports")


# --- Evaluation (pure) ------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemResult:
    """One system's ranking of a question and its scores."""

    ranking: tuple[str, ...]  # section hashes, best first
    source_ranks: tuple[int | None, ...]  # each required source's first rank; None if absent
    scores: Mapping[str, float]  # by the names in METRICS

    @property
    def first_rank(self) -> int | None:
        """The rank of the first required source found; None when none is."""
        return min((rank for rank in self.source_ranks if rank is not None), default=None)


@dataclass(frozen=True)
class QuestionResult:
    id: str
    category: str
    filters: SearchFilters
    required: tuple[frozenset[str], ...]
    unindexed_sources: int  # required sources none of whose sections is in the index
    systems: Mapping[str, SystemResult]  # by the names in SYSTEMS


@dataclass(frozen=True)
class Comparison:
    """A paired bootstrap of one system against a baseline over the same questions."""

    baseline: str
    system: str
    metric: str
    mean: float  # mean of system - baseline
    low: float  # 95% interval
    high: float


@dataclass(frozen=True)
class EvalReport:
    offline: bool  # an index in memory (`--offline`) rather than the database's
    created_at: datetime
    gold_path: str
    gold_sha256: str
    gold_questions: int
    index: IndexBuildInfo
    candidates: int
    rrf_k: int
    results: tuple[QuestionResult, ...]
    skipped: tuple[SkippedQuestion, ...]


def system_rankings(
    vector: Sequence[RankedChunk], text: Sequence[RankedChunk], rrf_k: int
) -> dict[str, list[str]]:
    """The section hashes each system ranks, best first, from the two branches' chunks."""
    return {
        "vector": [chunk.section_hash for chunk in section_ranking(vector)],
        "bm25": [chunk.section_hash for chunk in section_ranking(text)],
        "hybrid": [section.section_hash for section in fuse(vector, text, rrf_k)],
    }


def scores(ranking: Sequence[str], required: Sequence[frozenset[str]]) -> dict[str, float]:
    """Every metric of METRICS for one ranking."""
    values = {f"recall@{k}": recall_at(ranking, required, k) for k in CUTOFFS}
    values["mrr@10"] = reciprocal_rank(ranking, required, 10)
    values["ndcg@10"] = ndcg_at(ranking, required, 10)
    values["all_found@10"] = float(all_hit_at(ranking, required, 10))
    return values


def source_ranks(
    ranking: Sequence[str], required: Sequence[frozenset[str]]
) -> tuple[int | None, ...]:
    """Each required source's 1-based rank of first appearance in the whole ranking, or None."""
    first: dict[str, int] = {}
    for rank, key in enumerate(ranking, start=1):
        first.setdefault(key, rank)
    return tuple(
        min((first[key] for key in source if key in first), default=None) for source in required
    )


def evaluate_question(
    question: RetrievalQuestion,
    vector: Sequence[RankedChunk],
    text: Sequence[RankedChunk],
    rrf_k: int,
    indexed: Collection[str],
) -> QuestionResult:
    """Score the three systems' rankings of one question; `indexed` is every indexed hash."""
    required = question.required
    systems = {
        system: SystemResult(
            ranking=tuple(ranking),
            source_ranks=source_ranks(ranking, required),
            scores=scores(ranking, required),
        )
        for system, ranking in system_rankings(vector, text, rrf_k).items()
    }
    return QuestionResult(
        id=question.question.id,
        category=question.question.category,
        filters=question.filters,
        required=required,
        unindexed_sources=sum(1 for source in required if source.isdisjoint(indexed)),
        systems=systems,
    )


def mean_scores(results: Sequence[QuestionResult], system: str) -> dict[str, float]:
    """The mean of each metric over the questions."""
    return {
        metric: statistics.fmean(result.systems[system].scores[metric] for result in results)
        for metric in METRICS
    }


def by_category(results: Sequence[QuestionResult]) -> dict[str, list[QuestionResult]]:
    """The results per category, in the order the categories first appear."""
    groups: dict[str, list[QuestionResult]] = {}
    for result in results:
        groups.setdefault(result.category, []).append(result)
    return groups


def compare(results: Sequence[QuestionResult], baseline: str, system: str) -> Comparison:
    """The paired bootstrap of `system` against `baseline` on BOOTSTRAP_METRIC."""
    mean, low, high = paired_bootstrap(
        [result.systems[baseline].scores[BOOTSTRAP_METRIC] for result in results],
        [result.systems[system].scores[BOOTSTRAP_METRIC] for result in results],
        BOOTSTRAP_RESAMPLES,
        BOOTSTRAP_SEED,
    )
    return Comparison(baseline, system, BOOTSTRAP_METRIC, mean, low, high)


def comparisons(results: Sequence[QuestionResult]) -> list[Comparison]:
    return [compare(results, baseline, system) for baseline, system in COMPARISONS]


# --- Reports (pure) ---------------------------------------------------------------------------


def report_stem(report: EvalReport) -> str:
    """'retrieval-text-embedding-3-large-1536', with '-offline' for an index in memory."""
    model = re.sub(r"[^A-Za-z0-9._-]+", "-", report.index.embedding_model).strip("-")
    return f"retrieval-{model}{'-offline' if report.offline else ''}"


def report_json(report: EvalReport) -> str:
    """Every number of the report, the rankings as section hashes, and how the index was built."""
    results = report.results
    index = report.index
    data: dict[str, Any] = {
        "evaluation": "retrieval",
        "mode": "offline" if report.offline else "database",
        "created_at": report.created_at.isoformat(),
        "gold": {
            "path": report.gold_path,
            "sha256": report.gold_sha256,
            "questions": report.gold_questions,
            "evaluated": len(results),
            "skipped": [
                {"id": item.id, "category": item.category, "reason": item.reason.value}
                for item in report.skipped
            ],
        },
        "index": {
            "embedding_model": index.embedding_model,
            "analyser": index.analyser,
            "chunk_count": index.chunk_count,
            "held_back_count": index.held_back_count,
            "document_count": index.document_count,
            "term_count": index.term_count,
            "built_at": index.built_at.isoformat(),
        },
        "parameters": {
            "candidates": report.candidates,
            "rrf_k": report.rrf_k,
            "cutoffs": list(CUTOFFS),
            "ranking_depth": RANKING_DEPTH,
            "bootstrap": {
                "metric": BOOTSTRAP_METRIC,
                "resamples": BOOTSTRAP_RESAMPLES,
                "seed": BOOTSTRAP_SEED,
            },
        },
        "overall": {system: mean_scores(results, system) for system in SYSTEMS},
        "by_category": [
            {
                "category": category,
                "questions": len(group),
                "systems": {system: mean_scores(group, system) for system in SYSTEMS},
            }
            for category, group in by_category(results).items()
        ],
        "comparisons": [
            {
                "baseline": item.baseline,
                "system": item.system,
                "metric": item.metric,
                "mean_difference": item.mean,
                "low": item.low,
                "high": item.high,
            }
            for item in comparisons(results)
        ],
        "questions": [_question_json(result) for result in results],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _question_json(result: QuestionResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "category": result.category,
        "filters": {
            "framework_area": result.filters.framework_area,
            "agreement_number": result.filters.agreement_number,
        },
        "required_sources": [sorted(source) for source in result.required],
        "unindexed_sources": result.unindexed_sources,
        "systems": {
            system: {
                "first_rank": outcome.first_rank,
                "source_ranks": list(outcome.source_ranks),
                "scores": dict(outcome.scores),
                "ranking": list(outcome.ranking[:RANKING_DEPTH]),
            }
            for system, outcome in result.systems.items()
        },
    }


_SYSTEM_NAMES = {"vector": "vektor", "bm25": "BM25", "hybrid": "hybrid"}
_SKIP_REASONS = {
    SkipReason.REGISTER_ONLY: "svaret står bara i registret",
    SkipReason.NO_ANSWER: "avtalen besvarar inte frågan",
}
_METRIC_HEADERS = (*(f"R@{k}" for k in CUTOFFS), "MRR@10", "nDCG@10", "Alla@10")
_NBSP = "\u00a0"  # between thousands and before "%": "13 175", "95 %"
# Characters that would start markup in a table cell or a line.
_MARKUP = str.maketrans({char: "\\" + char for char in "\\`*_[]<>|"})


def render_markdown(report: EvalReport) -> str:
    """The report for a person, in Swedish: what was measured, how, and the numbers."""
    results = report.results
    lines = [
        f"# Mätning av sökningen: {_md(report.index.embedding_model)}"
        + (" (offline)" if report.offline else ""),
        "",
        "Guldfrågorna ställs till sökningens två grenar, vektor och BM25, och till deras "
        "sammanvägning (hybrid). Varje rangordning av avsnitt jämförs med de avsnitt som "
        "frågans svar behöver. Rapporten innehåller bara id, hashar och tal, ingen text ur "
        "frågorna eller dokumenten.",
        "",
    ]
    lines += _md_summary(report)
    lines += _md_run(report)
    lines += _md_method(report)
    lines += ["## Alla frågor", "", *_md_table(results, with_category=False), ""]
    lines += _md_comparisons(results)
    lines += ["## Per kategori", "", *_md_table(results, with_category=True), ""]
    lines += _md_questions(results)
    lines += _md_skipped(report)
    return "\n".join(lines).rstrip("\n") + "\n"


def _md_summary(report: EvalReport) -> list[str]:
    results = report.results
    ndcg = ", ".join(
        f"{_SYSTEM_NAMES[system]} {_decimal(mean_scores(results, system)['ndcg@10'])}"
        for system in SYSTEMS
    )
    lines = [
        "## Sammanfattning",
        "",
        f"- {len(results)} av {_count(report.gold_questions, 'fråga', 'frågor')} mättes; "
        f"{len(report.skipped)} hoppades över (se sist).",
        f"- nDCG@10: {ndcg}.",
    ]
    for item in comparisons(results):
        lines.append(
            f"- {_SYSTEM_NAMES[item.system].capitalize()} mot {_SYSTEM_NAMES[item.baseline]}: "
            f"{_signed(item.mean)} i nDCG@10 (95{_NBSP}% intervall {_signed(item.low)} till "
            f"{_signed(item.high)}), {_verdict(item)}."
        )
    unindexed = sum(result.unindexed_sources for result in results)
    if unindexed:
        lines.append(
            f"- {unindexed} {'källa' if unindexed == 1 else 'källor'} finns inte i indexet och "
            "kan inte hittas av någon sökning (se per fråga)."
        )
    return [*lines, ""]


def _md_run(report: EvalReport) -> list[str]:
    index = report.index
    where = (
        "index i minnet, byggt av de lagrade chunkarna (offline)"
        if report.offline
        else "sökindexet i databasen"
    )
    return [
        "## Körning",
        "",
        f"- **Tid:** {_moment(report.created_at)}",
        f"- **Index:** {where}, byggt {_moment(index.built_at)}",
        f"- **Embeddingmodell:** {_md(index.embedding_model)}",
        f"- **Textanalys:** {_md(index.analyser)}",
        f"- **Indexets storlek:** {_count(index.chunk_count, 'chunk', 'chunkar')} ur "
        f"{_count(index.document_count, 'fil', 'filer')}, {_n(index.term_count)} ord; "
        f"{_count(index.held_back_count, 'chunk', 'chunkar')} hålls tillbaka av karantänen",
        f"- **Guldfil:** {_md(report.gold_path)} (sha256 `{report.gold_sha256[:12]}`)",
        f"- **Kandidater per gren:** {_n(report.candidates)}",
        f"- **RRF k:** {report.rrf_k}",
        "",
    ]


def _md_method(report: EvalReport) -> list[str]:
    lines = [
        "## Vad som mättes och hur",
        "",
        "- Varje guldfråga med minst en dokumentkälla ställs med frågans avgränsning "
        "(ramavtalsområde och avtal) som filter. Vektorgrenen jämför frågans embedding med "
        "chunkarnas, BM25-grenen frågans ordstammar med chunkarnas, och varje gren lämnar sina "
        f"{_n(report.candidates)} bästa chunkar. Chunkarna förs ihop till avsnitt, där "
        "identiska kopior av ett avsnitt räknas som ett, och hybrid väger ihop grenarnas "
        f"rangordningar med reciprocal rank fusion (k = {report.rrf_k}). Det är samma "
        "funktioner som sökningen använder.",
        "- En källa är ett avsnitt som svaret behöver. Den räknas som hittad när något av dess "
        "godtagbara avsnitt (till exempel samma villkor i två dokument) finns i rangordningen. "
        "Guldfilens källor översätts till avsnittens hashar genom de lagrade avsnitten: "
        "avsnittet hittas på sitt nummer eller sin position och måste innehålla citatet "
        "ordagrant.",
        "- **R@k** (recall): andelen av frågans källor bland de k första avsnitten. "
        "**MRR@10**: 1 delat med rangen för den första hittade källan bland de 10 första, "
        "annars 0. **nDCG@10**: de hittade källorna vägda efter rang (1/log2(1 + rang)), "
        "delat med det bästa möjliga. **Alla@10**: andelen frågor där alla källor finns bland "
        "de 10 första. Alla tal är medelvärden över frågorna.",
        "- Hybrid jämförs med varje gren med en parad bootstrap på nDCG@10: frågorna dras om "
        f"med återläggning {_n(BOOTSTRAP_RESAMPLES)} gånger (frö {BOOTSTRAP_SEED}), och "
        "intervallet går från den 2,5:e till den 97,5:e percentilen av medelskillnaden. Ett "
        "intervall som inte innehåller 0 visar en skillnad som är större än slumpen; med så få "
        "frågor kan några hundradelar vara brus.",
    ]
    if report.offline:
        lines.append(
            "- Offline byggs indexet i minnet av de lagrade chunkarna, med embeddingar ur "
            "cachen (de som saknas embeddas och sparas i cachen), och rangordnar som sökningen "
            "i databasen. Sökindexet i databasen används inte."
        )
    lines.append(
        "- Frågor vars svar bara står i registret, och frågor som avtalen inte besvarar, har "
        "inget avsnitt att hitta. De hör till agentens utvärdering och listas sist."
    )
    return [*lines, ""]


def _md_table(results: Sequence[QuestionResult], with_category: bool) -> list[str]:
    head = ["Kategori", "Frågor"] if with_category else ["Frågor"]
    lines = [
        _row([*head, "System", *_METRIC_HEADERS]),
        _row(["---"] * len(head) + ["---"] + ["---:"] * len(_METRIC_HEADERS)),
    ]
    groups = by_category(results) if with_category else {"": list(results)}
    for category, group in groups.items():
        for system in SYSTEMS:
            means = mean_scores(group, system)
            first = [_md(category), str(len(group))] if with_category else [str(len(group))]
            lines.append(
                _row(
                    [
                        *first,
                        _SYSTEM_NAMES[system],
                        *(_decimal(means[metric]) for metric in METRICS),
                    ]
                )
            )
    return lines


def _md_comparisons(results: Sequence[QuestionResult]) -> list[str]:
    lines = [
        "## Hybrid jämförd med grenarna",
        "",
        _row(["Jämförelse", "Skillnad i nDCG@10", f"95{_NBSP}% intervall", "Bedömning"]),
        _row(["---", "---:", "---", "---"]),
    ]
    for item in comparisons(results):
        lines.append(
            _row(
                [
                    f"{_SYSTEM_NAMES[item.system]} − {_SYSTEM_NAMES[item.baseline]}",
                    _signed(item.mean),
                    f"{_signed(item.low)} till {_signed(item.high)}",
                    _verdict(item),
                ]
            )
        )
    return [*lines, ""]


def _md_questions(results: Sequence[QuestionResult]) -> list[str]:
    lines = [
        "## Per fråga",
        "",
        "Rangen där varje källa först finns i systemets rangordning, i guldfilens ordning "
        "(– när den inte finns bland kandidaterna).",
        "",
        _row(
            ["Fråga", "Kategori", "Källor", *(_SYSTEM_NAMES[s] for s in SYSTEMS), "nDCG@10 hybrid"]
        ),
        _row(["---", "---", "---:", *(["---"] * len(SYSTEMS)), "---:"]),
    ]
    for result in results:
        lines.append(
            _row(
                [
                    _md(result.id),
                    _md(result.category),
                    str(len(result.required)),
                    *(_ranks(result.systems[system].source_ranks) for system in SYSTEMS),
                    _decimal(result.systems["hybrid"].scores["ndcg@10"]),
                ]
            )
        )
    lines.append("")
    unindexed = [result for result in results if result.unindexed_sources]
    if unindexed:
        listed = ", ".join(f"{_md(r.id)} ({r.unindexed_sources})" for r in unindexed)
        lines.append(
            "Källor vars avsnitt inte finns i indexet (hålls tillbaka av karantänen eller "
            f"saknas), per fråga: {listed}."
        )
    else:
        lines.append("Alla källors avsnitt finns i indexet.")
    return [*lines, ""]


def _md_skipped(report: EvalReport) -> list[str]:
    lines = ["## Frågor som inte mättes", ""]
    if not report.skipped:
        return [*lines, "Alla frågor mättes."]
    lines += [_row(["Fråga", "Kategori", "Skäl"]), _row(["---", "---", "---"])]
    lines += [
        _row([_md(item.id), _md(item.category), _SKIP_REASONS[item.reason]])
        for item in report.skipped
    ]
    return lines


def _verdict(item: Comparison) -> str:
    if item.low == item.high == 0:
        return "ingen skillnad"
    if item.low > 0:
        return "bättre, större än slumpen"
    if item.high < 0:
        return "sämre, större än slumpen"
    return "kan vara slump"


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _ranks(ranks: Sequence[int | None]) -> str:
    return ", ".join("–" if rank is None else str(rank) for rank in ranks)


def _decimal(value: float) -> str:
    """A score the Swedish way: '0,731'."""
    return f"{value:.3f}".replace(".", ",")


def _signed(value: float) -> str:
    """A difference the Swedish way: '+0,042', '−0,010'."""
    text = _decimal(abs(value))
    return ("−" if value < 0 and text != "0,000" else "+") + text


def _n(value: int) -> str:
    """A whole number the Swedish way: '13 175' (with a no-break space)."""
    return f"{value:,}".replace(",", _NBSP)


def _count(value: int, one: str, many: str) -> str:
    """A count and its noun: '1 fil', '207 filer'."""
    return f"{_n(value)} {one if value == 1 else many}"


def _moment(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M:%S} (UTC)"


def _md(text: str) -> str:
    """Text for a table cell or a line: on one line, with markup characters escaped."""
    return " ".join(text.split()).translate(_MARKUP)


def overall_lines(report: EvalReport) -> list[str]:
    """The overall table as printed: one line per system, then the comparisons."""
    mode = "in-memory index (offline)" if report.offline else "database index"
    header = ["system", *(f"R@{k}" for k in CUTOFFS), "MRR@10", "nDCG@10", "all@10"]
    lines = [
        f"Retrieval evaluation, {mode}, {report.index.embedding_model}: "
        f"{len(report.results)} questions, {len(report.skipped)} skipped",
        "  ".join(f"{cell:>7}" if n else f"{cell:<7}" for n, cell in enumerate(header)),
    ]
    for system in SYSTEMS:
        means = mean_scores(report.results, system)
        values = [f"{means[metric]:.3f}" for metric in METRICS]
        lines.append("  ".join([f"{system:<7}", *(f"{value:>7}" for value in values)]))
    for item in comparisons(report.results):
        lines.append(
            f"{item.metric} {item.system} - {item.baseline}: {item.mean:+.3f} "
            f"(95% interval {item.low:+.3f} to {item.high:+.3f})"
        )
    unindexed = sum(result.unindexed_sources for result in report.results)
    if unindexed:
        lines.append(f"Required sources not in the index: {unindexed}")
    return lines


def write_reports(report: EvalReport, out_dir: Path) -> tuple[Path, Path]:
    """Write the Markdown and JSON reports to `out_dir`; return both paths."""
    stem = report_stem(report)
    markdown, data = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(report), encoding="utf-8")
        data.write_text(report_json(report), encoding="utf-8")
    except OSError as error:
        raise CommandError(f"cannot write the reports to {out_dir}: {error.strerror}") from None
    return markdown, data


# --- Runs (database and network) --------------------------------------------------------------


def database_run(
    factory: sessionmaker[Session],
    embedder: Embedder,
    gold: GoldFile,
    candidates: int,
    rrf_k: int,
) -> EvalReport:
    """Measure the index in PostgreSQL with the search's own branch queries."""
    with factory() as session:
        # The search's own check, so the evaluation refuses exactly what the search refuses.
        hybrid_search.check_index(session, embedder.name)
        build = current_build(session)
        questions, skipped = _resolved(gold, session)
        indexed = _indexed_hashes(session, questions)
    if build is None:  # only if the index was emptied between the two reads
        raise CommandError("the search index was emptied: build it again and rerun")
    _log.info(
        "Resolved %d questions to sections, skipped %d; embedding the questions with %s",
        len(questions),
        len(skipped),
        embedder.name,
    )
    # Embedded with no transaction open; the index must be the same afterwards.
    vectors = [embedder.embed_query(item.question.question) for item in questions]
    results: list[QuestionResult] = []
    with factory() as session:
        if current_build(session) != build:
            raise CommandError(
                "the search index was rebuilt while the questions were embedded: run the "
                "evaluation again"
            )
        for item, vector in zip(questions, vectors, strict=True):
            terms = swedish_text.query_terms(item.question.question)
            results.append(
                evaluate_question(
                    item,
                    vector_candidates(session, vector, item.filters, candidates),
                    text_candidates(session, terms, item.filters, candidates),
                    rrf_k,
                    indexed,
                )
            )
    return _report(False, gold, build, candidates, rrf_k, results, skipped)


def offline_run(
    factory: sessionmaker[Session],
    embedder: Embedder,
    gold: GoldFile,
    candidates: int,
    rrf_k: int,
    batch_size: int,
) -> EvalReport:
    """Measure an index built in memory from the stored chunks, with `embedder`'s vectors."""
    with factory() as session:
        plan, scopes = index_input(session)
        procurements = agreement_procurements(register_entries(session))
        questions, skipped = _resolved(gold, session)
        cached = cached_embeddings(
            session, embedder.name, {chunk.text_hash for chunk in plan.chunks}
        )
    if not plan.chunks:
        raise CommandError("there are no chunks to index: run `process` first")
    files = {chunk.key.sha256 for chunk in plan.chunks}
    unlinked = sorted(files - scopes.keys())
    if unlinked:
        raise CommandError(
            f"{len(unlinked)} files with chunks have no link from an agreement page any more "
            f"(e.g. {unlinked[0][:12]}): run `process` first"
        )
    missing = {
        chunk.text_hash: chunk.embedded_text
        for chunk in plan.chunks
        if chunk.text_hash not in cached
    }
    _log.info(
        f"Offline index: {len(plan.chunks):,} chunks, {len(cached):,} texts from the cache, "
        f"{len(missing):,} to embed with {embedder.name}"
    )
    embedded = embed_texts(factory, embedder, missing, batch_size)
    index = OfflineIndex.build(plan, cached | embedded, scopes, procurements)
    build = IndexBuildInfo(
        embedding_model=embedder.name,
        analyser=swedish_text.ANALYSER,
        term_count=len(index.term_ids),
        chunk_count=len(plan.chunks),
        held_back_count=plan.held_back,
        document_count=len(files),
        built_at=datetime.now(UTC),
    )
    indexed = set(index.section_hashes)
    results = [
        evaluate_question(
            item,
            index.vector_candidates(
                embedder.embed_query(item.question.question), item.filters, candidates
            ),
            index.text_candidates(
                swedish_text.query_terms(item.question.question), item.filters, candidates
            ),
            rrf_k,
            indexed,
        )
        for item in questions
    ]
    return _report(True, gold, build, candidates, rrf_k, results, skipped)


def _resolved(
    gold: GoldFile, session: Session
) -> tuple[list[RetrievalQuestion], list[SkippedQuestion]]:
    """The gold's retrieval questions resolved through the stored sections, and the skipped."""
    questions, skipped = retrieval_questions(gold.questions, DatabaseSections(session))
    if not questions:
        raise GoldError(f"{gold.path} has no question with a document source to measure")
    return questions, skipped


def _indexed_hashes(session: Session, questions: Sequence[RetrievalQuestion]) -> set[str]:
    """The questions' required section hashes that are in the search index."""
    wanted = sorted({key for item in questions for source in item.required for key in source})
    if not wanted:
        return set()
    chunk = models.SearchChunk
    return set(
        session.scalars(select(chunk.section_hash).distinct().where(chunk.section_hash.in_(wanted)))
    )


def _shown_path(path: Path) -> str:
    """The gold file's path for the reports: relative, so it names no home directory."""
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return path.name


def _report(
    offline: bool,
    gold: GoldFile,
    build: IndexBuildInfo,
    candidates: int,
    rrf_k: int,
    results: Sequence[QuestionResult],
    skipped: Sequence[SkippedQuestion],
) -> EvalReport:
    return EvalReport(
        offline=offline,
        created_at=datetime.now(UTC),
        gold_path=_shown_path(gold.path),
        gold_sha256=gold.sha256,
        gold_questions=len(gold.questions),
        index=build,
        candidates=candidates,
        rrf_k=rrf_k,
        results=tuple(results),
        skipped=tuple(skipped),
    )


# --- Command line -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_retrieval_eval",
        description="Measure the search (vector, BM25, hybrid) on the gold questions.",
    )
    parser.add_argument(
        "--gold", type=Path, default=DEFAULT_GOLD, help=f"the gold file; default {DEFAULT_GOLD}"
    )
    parser.add_argument(
        "--candidates",
        type=positive_int,
        help="chunks each branch passes to the fusion; default SEARCH_CANDIDATES",
    )
    parser.add_argument(
        "--rrf-k", type=positive_int, help="k of reciprocal rank fusion; default RRF_K"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"where the reports are written; default {DEFAULT_OUT}",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="build the index in memory from the stored chunks instead of using the database's",
    )
    parser.add_argument(
        "--model", help="with --offline: the embedding model; default EMBEDDING_MODEL"
    )
    parser.add_argument(
        "--dimensions",
        type=positive_int,
        help="with --offline: the embedding dimensions; default EMBEDDING_DIMENSIONS",
    )
    return parser


def run_settings(settings: Settings, model: str | None, dimensions: int | None) -> Settings:
    """The settings with the embedding model and dimensions of `--model` and `--dimensions`."""
    changes: dict[str, Any] = {}
    if model is not None:
        changes["embedding_model"] = model
    if dimensions is not None:
        changes["embedding_dimensions"] = dimensions
    return settings.model_copy(update=changes)


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.offline and (args.model is not None or args.dimensions is not None):
        parser.error("--model and --dimensions need --offline: the database index has its own")
    settings = get_settings()
    configure_logging(settings.log_level)
    logging.getLogger("evals").setLevel(settings.log_level.upper())
    candidates: int = args.candidates or settings.search_candidates
    rrf_k: int = args.rrf_k or settings.rrf_k
    try:
        gold = load_gold(args.gold)
        embedder = require_embedder(run_settings(settings, args.model, args.dimensions))
        factory = session_factory(create_db_engine())
        if args.offline:
            report = offline_run(
                factory, embedder, gold, candidates, rrf_k, settings.embedding_batch_size
            )
        else:
            report = database_run(factory, embedder, gold, candidates, rrf_k)
        markdown, data = write_reports(report, args.out)
    except (GoldError, CommandError, IndexNotReadyError, EmbeddingError) as error:
        # The messages name the error type, the model or the file, never the key.
        _log.error("retrieval evaluation: %s", error)
        raise SystemExit(1) from None
    except OperationalError:
        # Not the driver's message: it can hold the database address.
        _log.error("retrieval evaluation: the database cannot be reached; is it running?")
        raise SystemExit(1) from None
    for line in overall_lines(report):
        print(line)
    print(f"Reports: {markdown} and {data}")


if __name__ == "__main__":
    main()
