"""Compare two runs of the answer evaluation on the same gold file: the agent and the baseline.

What:
    `python -m evals.compare_answer_runs A.json B.json [--out DIR]` reads
    two JSON reports of `run_answer_eval` (`load_run`), refuses them unless
    they were measured on the same gold file and the same questions
    (`check_comparable`), and writes a Swedish Markdown comparison
    (`render_comparison`) to `compare-<A>-vs-<B>.md` in DIR: for all
    questions and per category, each run's right-score, verified share,
    gold sources cited, median seconds, median model calls and cost; the
    paired difference B - A with its 95 % bootstrap interval for the
    right-score and the gold sources cited; each question's two verdicts
    side by side; and the questions to the user, when the gold file says
    whether the agent should ask.

        uv run python -m evals.compare_answer_runs agent.json workflow.json

Why:
    "The agent beats a fixed workflow" needs both on the same questions,
    scored the same way, and a difference that is more than chance: with
    30 questions one question is three points, so the comparison pairs the
    questions and bootstraps the difference (`metrics.paired_bootstrap`, as
    the search's variants are compared). Reports of different gold files,
    or of different questions (`--only`), would compare different things,
    so they are refused (ADR 0024).

How:
    Only the JSON reports are read, so a comparison can be made long after
    the runs, on the machine they were written on (the reports hold the
    answers and stay out of git). A verdict scores as `answer_scores.
    verdict_score` gives (correct 1, partly correct 0.5, incorrect 0); a
    question unjudged in a run (no judge, no verdict, or an error) has no
    score there, and the paired difference counts the questions judged in
    both. Gold sources cited are compared per question as the share of its
    sources cited, over the questions that have document sources. Which run
    is the agent and which the baseline comes from each report's mode (a
    report made before the mode was recorded is the agent's). Numbers are
    written the Swedish way, as in `answer_report`.
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.answer_report import UNJUDGED_NAME, VERDICT_NAMES
from evals.answer_scores import verdict_score
from evals.judge import Verdict
from evals.metrics import paired_bootstrap

DEFAULT_OUT = Path("evals/reports")
MODE_NAMES = {"agent": "agenten", "workflow": "baslinjen"}
OVERALL = "Alla frågor"


class CompareError(ValueError):
    """The reports cannot be read, or do not measure the same questions."""


@dataclass(frozen=True)
class QuestionRow:
    """One question of a run, as its JSON report has it."""

    id: str
    category: str
    verdict: Verdict | None
    status: str | None
    sources_found: int
    document_sources: int
    seconds: float
    model_calls: int | None
    cost: tuple[float, float] | None
    error: str | None

    @property
    def score(self) -> float | None:
        return verdict_score(self.verdict)

    @property
    def sources_share(self) -> float | None:
        """The share of the gold's document sources cited; None without any."""
        return self.sources_found / self.document_sources if self.document_sources else None


@dataclass(frozen=True)
class RunReport:
    """A run's JSON report, as far as the comparison reads it."""

    path: Path
    mode: str  # "agent" or "workflow"
    agent_model: str
    agent_effort: str
    label: str | None
    gold_sha256: str
    gold_path: str
    questions: tuple[QuestionRow, ...]
    asks: Mapping[str, int] | None  # answer_scores.AskSummary, when the gold says

    @property
    def name(self) -> str:
        """'agenten (gpt-6.1-sol, low)'."""
        label = f", {self.label}" if self.label else ""
        who = MODE_NAMES.get(self.mode, self.mode)
        return f"{who} ({self.agent_model}, {self.agent_effort}{label})"


def load_run(path: Path) -> RunReport:
    """The JSON report at `path`; `CompareError` if it cannot be read as one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        run, gold = data["run"], data["gold"]
        return RunReport(
            path=path,
            mode=str(run.get("mode") or "agent"),
            agent_model=str(run["agent_model"]),
            agent_effort=str(run["agent_effort"]),
            label=run.get("label"),
            gold_sha256=str(gold["sha256"]),
            gold_path=str(gold["path"]),
            questions=tuple(_row_of(item) for item in data["questions"]),
            asks=data.get("asks"),
        )
    except OSError as error:
        raise CompareError(f"cannot read {path}: {error.strerror}") from None
    except (ValueError, KeyError, TypeError) as error:
        raise CompareError(f"{path} is not a report of run_answer_eval ({error!r})") from None


def _row_of(item: Mapping[str, Any]) -> QuestionRow:
    cost = item.get("cost")
    return QuestionRow(
        id=str(item["id"]),
        category=str(item["category"]),
        verdict=item.get("verdict"),
        status=item.get("status"),
        sources_found=sum(1 for found in item.get("sources_found") or [] if found),
        document_sources=int(item.get("document_sources") or 0),
        seconds=float(item.get("seconds") or 0.0),
        model_calls=item.get("model_calls"),
        cost=(float(cost["low"]), float(cost["high"])) if cost else None,
        error=item.get("error"),
    )


def check_comparable(a: RunReport, b: RunReport) -> None:
    """Refuse two reports of different gold files, or of different questions."""
    if a.gold_sha256 != b.gold_sha256:
        raise CompareError(
            f"the reports are of different gold files ({a.gold_sha256[:12]} and "
            f"{b.gold_sha256[:12]}): compare runs of the same file"
        )
    a_ids, b_ids = {q.id for q in a.questions}, {q.id for q in b.questions}
    if a_ids != b_ids:
        only_a, only_b = sorted(a_ids - b_ids), sorted(b_ids - a_ids)
        raise CompareError(
            "the reports have different questions "
            f"(only in A: {', '.join(only_a) or 'none'}; only in B: {', '.join(only_b) or 'none'})"
        )


# --- The numbers ------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupNumbers:
    """A run's numbers for a group of questions."""

    questions: int
    judged: int
    right: float  # the sum of the judged questions' scores
    verified: int
    sources_found: int
    document_sources: int
    median_seconds: float
    median_model_calls: float | None  # None when no question saved them
    cost: tuple[float, float] | None  # None when a question has no cost


def group_numbers(rows: Sequence[QuestionRow]) -> GroupNumbers:
    scores = [score for row in rows if (score := row.score) is not None]
    calls = [row.model_calls for row in rows if row.model_calls is not None]
    costs = [row.cost for row in rows]
    return GroupNumbers(
        questions=len(rows),
        judged=len(scores),
        right=sum(scores),
        verified=sum(1 for row in rows if row.status == "verified"),
        sources_found=sum(row.sources_found for row in rows),
        document_sources=sum(row.document_sources for row in rows),
        median_seconds=statistics.median([row.seconds for row in rows]) if rows else 0.0,
        median_model_calls=statistics.median(calls) if calls else None,
        cost=(
            (sum(c[0] for c in costs if c), sum(c[1] for c in costs if c))
            if costs and all(c is not None for c in costs)
            else None
        ),
    )


@dataclass(frozen=True)
class Difference:
    """The mean of B - A over the questions with a value in both, and its 95 % interval."""

    mean: float
    low: float
    high: float
    questions: int


def paired_difference(
    a: Sequence[QuestionRow],
    b: Sequence[QuestionRow],
    value: Callable[[QuestionRow], float | None],
) -> Difference | None:
    """B - A of `value`, paired by question id; None when no question has both values."""
    by_id = {row.id: row for row in b}
    pairs = [
        (x, y)
        for row in a
        if (x := value(row)) is not None and (y := value(by_id[row.id])) is not None
    ]
    if not pairs:
        return None
    mean, low, high = paired_bootstrap([x for x, _ in pairs], [y for _, y in pairs])
    return Difference(mean, low, high, len(pairs))


# --- The Markdown -----------------------------------------------------------------------------


def render_comparison(a: RunReport, b: RunReport) -> str:
    """The comparison in Swedish Markdown; the reports must be comparable."""
    lines = [
        f"# Jämförelse: {_md(a.name)} och {_md(b.name)}",
        "",
        f"- **A:** {_md(a.name)}, `{_md(a.path.name)}`",
        f"- **B:** {_md(b.name)}, `{_md(b.path.name)}`",
        f"- **Guldfil:** {_md(a.gold_path)} (sha256 `{a.gold_sha256[:12]}`), "
        f"{len(a.questions)} frågor i båda",
        "",
        "Rätt räknas som i rapporterna: rätt 1, delvis rätt 0,5, fel 0, och en fråga utan "
        "bedömning räknas inte. B−A är medelskillnaden per fråga i procentenheter (p.e.), "
        "parad på frågan, med ett 95 %-intervall ur 10 000 bootstrapdragningar; ett intervall "
        "som inte innehåller 0 är en skillnad utöver slumpen. Facits källor jämförs per fråga "
        "som andelen av frågans källor som svaret citerar, i frågorna som har källor i "
        "dokumenten.",
        "",
    ]
    lines += _md_group(OVERALL, a.questions, b.questions)
    categories = list(dict.fromkeys(row.category for row in a.questions))
    if len(categories) > 1:
        lines += ["## Per kategori", ""]
        for category in categories:
            lines += _md_group(
                category,
                [row for row in a.questions if row.category == category],
                [row for row in b.questions if row.category == category],
                level="###",
            )
    lines += _md_questions(a, b)
    lines += _md_asks(a, b)
    return "\n".join(lines).rstrip("\n") + "\n"


def _md_group(
    title: str, a: Sequence[QuestionRow], b: Sequence[QuestionRow], level: str = "##"
) -> list[str]:
    x, y = group_numbers(a), group_numbers(b)
    right = paired_difference(a, b, lambda row: row.score)
    sources = paired_difference(a, b, lambda row: row.sources_share)
    lines = [
        f"{level} {_md(title)} ({x.questions} frågor)",
        "",
        _row(["Mått", "A", "B", "B−A (95 %)"]),
        _row(["---", "---:", "---:", "---:"]),
        _row(["Rätt (poäng)", _right(x), _right(y), _difference(right)]),
        _row(
            [
                "Kontrollerade svar",
                f"{x.verified} av {x.questions} ({_percent(x.verified, x.questions)})",
                f"{y.verified} av {y.questions} ({_percent(y.verified, y.questions)})",
                "–",
            ]
        ),
    ]
    if x.document_sources:
        lines.append(
            _row(
                [
                    "Facits källor citerade",
                    _sources(x),
                    _sources(y),
                    _difference(sources),
                ]
            )
        )
    lines += [
        _row(
            [
                "Tid per fråga (median)",
                _seconds(x.median_seconds),
                _seconds(y.median_seconds),
                "–",
            ]
        ),
        _row(
            [
                "Modellanrop per fråga (median)",
                _calls(x.median_model_calls),
                _calls(y.median_model_calls),
                "–",
            ]
        ),
        _row(["Kostnad, agent och granskare", _cost(x.cost), _cost(y.cost), "–"]),
        "",
    ]
    return lines


def _md_questions(a: RunReport, b: RunReport) -> list[str]:
    by_id = {row.id: row for row in b.questions}
    lines = [
        "## Per fråga",
        "",
        _row(["Fråga", "Kategori", "A", "B", "Skillnad", "Källor A", "Källor B"]),
        _row(["---", "---", "---", "---", "---", "---:", "---:"]),
    ]
    differ = 0
    for x in a.questions:
        y = by_id[x.id]
        changed = x.verdict != y.verdict
        differ += changed
        lines.append(
            _row(
                [
                    _md(x.id),
                    _md(x.category),
                    _verdict(x),
                    _verdict(y),
                    "≠" if changed else "",
                    _question_sources(x),
                    _question_sources(y),
                ]
            )
        )
    lines += [
        "",
        f"Bedömningen skiljer sig i {differ} av {len(a.questions)} frågor (≠).",
        "",
    ]
    return lines


def _md_asks(a: RunReport, b: RunReport) -> list[str]:
    if a.asks is None and b.asks is None:
        return []
    return [
        "## Motfrågor",
        "",
        _row(["Mått", "A", "B"]),
        _row(["---", "---:", "---:"]),
        _row(["Frågade när den borde", _asked(a.asks), _asked(b.asks)]),
        _row(["Frågade i onödan", _unnecessary(a.asks), _unnecessary(b.asks)]),
        "",
        "Frågade när den borde: frågor där testsamlingen säger att agenten ska fråga "
        "användaren, och i parentes de där domaren fann att motfrågan skiljer alternativen åt. "
        "Baslinjen kan inte fråga.",
        "",
    ]


def _asked(asks: Mapping[str, int] | None) -> str:
    if asks is None:
        return "–"
    return f"{asks['asked']} av {asks['should_ask']} ({asks['separating']} skiljer)"


def _unnecessary(asks: Mapping[str, int] | None) -> str:
    if asks is None:
        return "–"
    return f"{asks['asked_unnecessarily']} av {asks['should_not_ask']}"


def _right(numbers: GroupNumbers) -> str:
    text = f"{_decimal(numbers.right)} av {numbers.judged}"
    if numbers.judged:
        text += f" ({round(100 * numbers.right / numbers.judged)}{_NBSP}%)"
    if unjudged := numbers.questions - numbers.judged:
        text += f"; {unjudged} ej bedömda"
    return text


def _sources(numbers: GroupNumbers) -> str:
    found, total = numbers.sources_found, numbers.document_sources
    return f"{found} av {total} ({_percent(found, total)})"


def _difference(difference: Difference | None) -> str:
    """'−12 p.e. (−25 till +1)': the mean per question in percentage points, and its interval."""
    if difference is None:
        return "–"
    mean, low, high = (100 * value for value in (difference.mean, difference.low, difference.high))
    return f"{_signed(mean)}{_NBSP}p.e. ({_signed(low)} till {_signed(high)})"


def _verdict(row: QuestionRow) -> str:
    if row.verdict is not None:
        return VERDICT_NAMES[row.verdict]
    return "Fel i körningen" if row.error else UNJUDGED_NAME


def _question_sources(row: QuestionRow) -> str:
    return f"{row.sources_found} av {row.document_sources}" if row.document_sources else "–"


def _calls(value: float | None) -> str:
    if value is None:
        return "inte sparat"
    return str(int(value)) if value == int(value) else _decimal(value, 1)


_NBSP = " "
_MARKUP = str.maketrans({char: "\\" + char for char in "\\`*_[]<>|"})


def _md(text: str) -> str:
    return " ".join(text.split()).translate(_MARKUP)


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _decimal(value: float, places: int = 1) -> str:
    text = f"{value:.{places}f}".replace(".", ",")
    return text.removesuffix(",0") if places == 1 else text


def _signed(value: float) -> str:
    """A whole number with its sign: '+3', '−12', '+0'."""
    rounded = round(value)
    return ("−" if rounded < 0 else "+") + str(abs(rounded))


def _percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole) if whole else 0}{_NBSP}%"


def _seconds(value: float) -> str:
    return f"{round(value)}{_NBSP}s"


def _cost(dollars: tuple[float, float] | None) -> str:
    if dollars is None:
        return "–"
    low, high = (f"{value:.2f}".replace(".", ",") for value in dollars)
    return f"{high}{_NBSP}USD" if low == high else f"{low}–{high}{_NBSP}USD"


# --- Command line -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.compare_answer_runs",
        description="Compare two JSON reports of the answer evaluation on the same gold file.",
    )
    parser.add_argument("a", type=Path, help="the first run's JSON report (A)")
    parser.add_argument("b", type=Path, help="the second run's JSON report (B)")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"where the comparison is written; default {DEFAULT_OUT}",
    )
    return parser


def comparison_path(a: RunReport, b: RunReport, out_dir: Path) -> Path:
    return out_dir / f"compare-{a.path.stem}-vs-{b.path.stem}.md"


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        a, b = load_run(args.a), load_run(args.b)
        check_comparable(a, b)
        target = comparison_path(a, b, args.out)
        args.out.mkdir(parents=True, exist_ok=True)
        target.write_text(render_comparison(a, b), encoding="utf-8")
    except CompareError as error:
        print(f"compare_answer_runs: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError as error:
        print(f"compare_answer_runs: cannot write to {args.out}: {error.strerror}", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"Comparison: {target}")


if __name__ == "__main__":
    main()
