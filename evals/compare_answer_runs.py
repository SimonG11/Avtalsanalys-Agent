"""Compare two runs of the answer evaluation on the same gold file: the agent and the baseline.

What:
    `python -m evals.compare_answer_runs A.json B.json [--out DIR]` reads
    two JSON reports of `run_answer_eval` (`load_run`), refuses them unless
    they were measured on the same gold file and the same questions, by the
    same judge (its model, effort and prompts) and against the same
    avtal-mcp (`check_comparable`), and writes a Swedish Markdown
    comparison (`render_comparison`) to `compare-<A>-vs-<B>.md` in DIR:
    the settings in which the runs differ; for all questions and per
    category, each run's right-score, verified share, gold sources cited,
    median seconds, median model calls and cost; the paired difference
    B - A with the questions it pairs and, over at least
    MIN_PAIRS_FOR_INTERVAL questions, its 95 % bootstrap interval, for the
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
    so they are refused, and so are reports of different judges or servers,
    whose scores are measured differently (ADR 0024). Any other setting
    that differs (the model, the effort, the reviewer, the bounds) is
    listed, since it may be the point of the comparison.

How:
    Only the JSON reports are read, so a comparison can be made long after
    the runs, on the machine they were written on (the reports hold the
    answers and stay out of git). A verdict scores as `answer_scores.
    verdict_score` gives (correct 1, partly correct 0.5, incorrect 0); a
    question the run gave no answer to (an error or a timeout) scores 0
    when the run had a judge, as its own report counts it, so a run that
    fails more is not flattered; an answer left unjudged (no judge, or no
    verdict) has no score there, nor has any question of a run without a
    judge, and the paired difference counts the questions scored in both.
    Gold sources cited are compared per question as the share of its
    sources cited, over the questions that have document sources. On fewer
    than MIN_PAIRS_FOR_INTERVAL questions (most categories) the
    percentile bootstrap says nothing (on three questions all of one sign
    its interval has no width), so only the mean is shown. Which run is the
    agent and which the baseline comes from each report's mode (a report
    made before the mode was recorded is the agent's). The questions to the
    user are counted from each question's row by `answer_scores.
    summarize_asks`, as the run's own report counts them: a question whose
    run never reached the graph (no model calls saved) and did not ask is
    in neither count. The report's own `asks` is not read, so a report
    written before that rule is counted the same way. Numbers are written
    with `report_text`, as in `answer_report`.
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals.answer_report import UNJUDGED_NAME, VERDICT_NAMES
from evals.answer_scores import summarize_asks, verdict_score
from evals.judge import Verdict
from evals.metrics import paired_bootstrap
from evals.report_text import (
    NBSP,
    dollar_range,
    in_seconds,
    md,
    md_row,
    percent,
    plain_number,
)

DEFAULT_OUT = Path("evals/reports")
MODE_NAMES = {"agent": "agenten", "workflow": "baslinjen"}
OVERALL = "Alla frågor"
# The fewest paired questions an interval is shown for: on fewer it says nothing.
MIN_PAIRS_FOR_INTERVAL = 10
UNKNOWN = "okänt"


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
    judged: bool  # whether the run had a judge
    should_ask: bool | None = None  # the gold's; None when it does not say
    asked: bool = False  # whether the agent asked the user
    asked_right: bool | None = None  # as `answer_scores.QuestionResult.asked_right`

    @property
    def could_ask(self) -> bool:
        """Whether the agent had the chance to ask: the run reached the graph, or it asked."""
        return self.model_calls is not None or self.asked

    @property
    def score(self) -> float | None:
        """The verdict's score; None for an answer not judged.

        A question the run gave no answer to (an error) scores 0 when the run had a
        judge, as its report counts it, and None without one, like every answer of the run.
        """
        if self.status is None:
            return 0.0 if self.judged else None
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
    asks: Mapping[str, int] | None  # answer_scores.AskSummary of the rows, when the gold says
    # How the run was made, as its report's RunInfo gives it; None where it does not.
    judge_model: str | None = None  # also None without a judge (--no-judge)
    judge_effort: str | None = None
    judge_prompt_sha256: str | None = None  # also None in a report from before it was saved
    reviewer_model: str | None = None
    reviewer_effort: str | None = None
    reviewer_prompt_sha256: str | None = None
    mcp: str | None = None
    validation_retries: int | None = None
    model_call_limit: int | None = None
    timeout: float | None = None

    @property
    def name(self) -> str:
        """'agenten (gpt-6.1-sol, low)'."""
        label = f", {self.label}" if self.label else ""
        who = MODE_NAMES.get(self.mode, self.mode)
        return f"{who} ({self.agent_model}, {self.agent_effort}{label})"

    @property
    def judge(self) -> str:
        """'gpt-6-astra, resonemang medium', or that there was none."""
        if self.judge_model is None:
            return "ingen (--no-judge)"
        return f"{self.judge_model}, resonemang {self.judge_effort or UNKNOWN}"

    def settings(self) -> dict[str, str]:
        """The run's settings by their Swedish names, as the comparison lists them."""
        reviewer_prompt = self.reviewer_prompt_sha256
        return {
            "modell": self.agent_model,
            "resonemang": self.agent_effort,
            "granskare": f"{self.reviewer_model or UNKNOWN}, "
            f"resonemang {self.reviewer_effort or UNKNOWN}",
            "granskarens prompt": reviewer_prompt[:12] if reviewer_prompt else UNKNOWN,
            "domare": self.judge,
            "avtal-mcp": self.mcp or UNKNOWN,
            "nya försök": _known(self.validation_retries),
            "gräns för modellanrop": _known(self.model_call_limit),
            "tidsgräns": in_seconds(self.timeout) if self.timeout is not None else UNKNOWN,
        }


def _known(value: int | None) -> str:
    return str(value) if value is not None else UNKNOWN


def load_run(path: Path) -> RunReport:
    """The JSON report at `path`; `CompareError` if it cannot be read as one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        run, gold = data["run"], data["gold"]
        questions = tuple(
            _row_of(item, judged=run.get("judge_model") is not None) for item in data["questions"]
        )
        asks = summarize_asks(questions)
        return RunReport(
            path=path,
            mode=str(run.get("mode") or "agent"),
            agent_model=str(run["agent_model"]),
            agent_effort=str(run["agent_effort"]),
            label=run.get("label"),
            gold_sha256=str(gold["sha256"]),
            gold_path=str(gold["path"]),
            questions=questions,
            asks=asdict(asks) if asks is not None else None,
            judge_model=run.get("judge_model"),
            judge_effort=run.get("judge_effort"),
            judge_prompt_sha256=run.get("judge_prompt_sha256"),
            reviewer_model=run.get("reviewer_model"),
            reviewer_effort=run.get("reviewer_effort"),
            reviewer_prompt_sha256=run.get("reviewer_prompt_sha256"),
            mcp=run.get("mcp"),
            validation_retries=run.get("validation_retries"),
            model_call_limit=run.get("model_call_limit"),
            timeout=run.get("timeout"),
        )
    except OSError as error:
        raise CompareError(f"cannot read {path}: {error.strerror}") from None
    except (ValueError, KeyError, TypeError) as error:
        raise CompareError(f"{path} is not a report of run_answer_eval ({error!r})") from None


def _row_of(item: Mapping[str, Any], judged: bool) -> QuestionRow:
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
        judged=judged,
        should_ask=item.get("should_ask"),
        asked=bool(item.get("asked")),
        asked_right=item.get("asked_right"),
    )


def check_comparable(a: RunReport, b: RunReport) -> None:
    """Refuse reports of different gold files or questions, judges or avtal-mcp servers.

    A judge is its model, its effort and its prompts' hash.
    """
    if a.gold_sha256 != b.gold_sha256:
        raise CompareError(
            f"the reports are of different gold files ({a.gold_sha256[:12]} and "
            f"{b.gold_sha256[:12]}): compare runs of the same file"
        )
    judges = [(r.judge_model, r.judge_effort, r.judge_prompt_sha256) for r in (a, b)]
    if judges[0] != judges[1]:
        raise CompareError(
            f"the reports were judged differently ({_judged_by(a)} and {_judged_by(b)}): "
            "compare runs with the same judge"
        )
    if a.mcp != b.mcp:
        raise CompareError(
            f"the reports measured different avtal-mcp servers ({a.mcp} and {b.mcp}): "
            "compare runs against the same server"
        )
    a_ids, b_ids = {q.id for q in a.questions}, {q.id for q in b.questions}
    if a_ids != b_ids:
        only_a, only_b = sorted(a_ids - b_ids), sorted(b_ids - a_ids)
        raise CompareError(
            "the reports have different questions "
            f"(only in A: {', '.join(only_a) or 'none'}; only in B: {', '.join(only_b) or 'none'})"
        )


def _judged_by(run: RunReport) -> str:
    """How the run was judged, for a refusal: 'gpt-6-astra medium, prompts 1a2b3c4d5e6f'."""
    prompts = run.judge_prompt_sha256[:12] if run.judge_prompt_sha256 else "unknown"
    return f"{run.judge_model} {run.judge_effort}, prompts {prompts}"


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
        f"# Jämförelse: {md(a.name)} och {md(b.name)}",
        "",
        f"- **A:** {md(a.name)}, `{md(a.path.name)}`",
        f"- **B:** {md(b.name)}, `{md(b.path.name)}`",
        f"- **Guldfil:** {md(a.gold_path)} (sha256 `{a.gold_sha256[:12]}`), "
        f"{len(a.questions)} frågor i båda",
        f"- **Domare:** {md(a.judge)}; **avtal-mcp:** {md(a.mcp or UNKNOWN)}",
        _md_settings(a, b),
        "",
        "Rätt räknas som rätt 1, delvis rätt 0,5 och fel 0; en fråga utan svar (fel i "
        "körningen) räknas som fel, och ett svar som domaren inte bedömde räknas inte. B−A är "
        "medelskillnaden per fråga i procentenheter (p.e.), parad på frågan, med antalet frågor "
        "som har ett värde i båda och, över minst "
        f"{MIN_PAIRS_FOR_INTERVAL} frågor, ett 95{NBSP}%-intervall ur 10{NBSP}000 "
        "bootstrapdragningar; ett intervall som inte innehåller 0 är en skillnad utöver slumpen. "
        "På färre frågor säger ett sådant intervall inget, så där står bara medelskillnaden. "
        "Facits källor jämförs per fråga som andelen av frågans källor som svaret citerar, i "
        "frågorna som har källor i dokumenten.",
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


def _md_settings(a: RunReport, b: RunReport) -> str:
    """The settings in which the runs differ, or that they are the same."""
    x, y = a.settings(), b.settings()
    differ = [f"{name} (A: {md(x[name])}, B: {md(y[name])})" for name in x if x[name] != y[name]]
    if not differ:
        return (
            "- **Körningarna:** samma modell, resonemang, granskare, domare, avtal-mcp och gränser"
        )
    return f"- **Körningarna skiljer sig i:** {'; '.join(differ)}"


def _md_group(
    title: str, a: Sequence[QuestionRow], b: Sequence[QuestionRow], level: str = "##"
) -> list[str]:
    x, y = group_numbers(a), group_numbers(b)
    right = paired_difference(a, b, lambda row: row.score)
    sources = paired_difference(a, b, lambda row: row.sources_share)
    lines = [
        f"{level} {md(title)} ({x.questions} frågor)",
        "",
        md_row(["Mått", "A", "B", "B−A"]),
        md_row(["---", "---:", "---:", "---:"]),
        md_row(["Rätt (poäng)", _right(x), _right(y), _difference(right)]),
        md_row(
            [
                "Kontrollerade svar",
                f"{x.verified} av {x.questions} ({percent(x.verified, x.questions)})",
                f"{y.verified} av {y.questions} ({percent(y.verified, y.questions)})",
                "–",
            ]
        ),
    ]
    if x.document_sources:
        lines.append(
            md_row(
                [
                    "Facits källor citerade",
                    _sources(x),
                    _sources(y),
                    _difference(sources),
                ]
            )
        )
    lines += [
        md_row(
            [
                "Tid per fråga (median)",
                in_seconds(x.median_seconds),
                in_seconds(y.median_seconds),
                "–",
            ]
        ),
        md_row(
            [
                "Modellanrop per fråga (median)",
                _calls(x.median_model_calls),
                _calls(y.median_model_calls),
                "–",
            ]
        ),
        md_row(["Kostnad, agent och granskare", _cost(x.cost), _cost(y.cost), "–"]),
        "",
    ]
    return lines


def _md_questions(a: RunReport, b: RunReport) -> list[str]:
    by_id = {row.id: row for row in b.questions}
    lines = [
        "## Per fråga",
        "",
        md_row(["Fråga", "Kategori", "A", "B", "Skillnad", "Källor A", "Källor B"]),
        md_row(["---", "---", "---", "---", "---", "---:", "---:"]),
    ]
    differ = 0
    for x in a.questions:
        y = by_id[x.id]
        changed = x.verdict != y.verdict
        differ += changed
        lines.append(
            md_row(
                [
                    md(x.id),
                    md(x.category),
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
    baseline = " Baslinjen kan inte fråga." if "workflow" in (a.mode, b.mode) else ""
    return [
        "## Motfrågor",
        "",
        md_row(["Mått", "A", "B"]),
        md_row(["---", "---:", "---:"]),
        md_row(["Frågade när den borde", _asked(a.asks), _asked(b.asks)]),
        md_row(["Frågade i onödan", _unnecessary(a.asks), _unnecessary(b.asks)]),
        "",
        "Frågade när den borde: frågor där testsamlingen säger att agenten ska fråga "
        "användaren, och i parentes de där domaren fann att motfrågan skiljer alternativen åt "
        "och de motfrågor den inte bedömde. En fråga där körningen aldrig nådde agenten räknas "
        "inte, så A och B kan ha olika många frågor." + baseline,
        "",
    ]


def _asked(asks: Mapping[str, int] | None) -> str:
    if asks is None:
        return "–"
    text = f"{asks['asked']} av {asks['should_ask']} ({asks['separating']} skiljer"
    if unjudged := asks.get("unjudged"):
        text += f", {unjudged} ej bedömda"
    return text + ")"


def _unnecessary(asks: Mapping[str, int] | None) -> str:
    if asks is None:
        return "–"
    return f"{asks['asked_unnecessarily']} av {asks['should_not_ask']}"


def _right(numbers: GroupNumbers) -> str:
    text = f"{plain_number(numbers.right)} av {numbers.judged}"
    if numbers.judged:
        text += f" ({round(100 * numbers.right / numbers.judged)}{NBSP}%)"
    if unjudged := numbers.questions - numbers.judged:
        text += f"; {unjudged} ej bedömda"
    return text


def _sources(numbers: GroupNumbers) -> str:
    found, total = numbers.sources_found, numbers.document_sources
    return f"{found} av {total} ({percent(found, total)})"


def _difference(difference: Difference | None) -> str:
    """'−12 p.e. (−25 till +1; 30 frågor)': the mean per question in percentage points.

    The interval only over at least MIN_PAIRS_FOR_INTERVAL questions.
    """
    if difference is None:
        return "–"
    mean, low, high = (100 * value for value in (difference.mean, difference.low, difference.high))
    pairs = f"{difference.questions} {'fråga' if difference.questions == 1 else 'frågor'}"
    if difference.questions < MIN_PAIRS_FOR_INTERVAL:
        return f"{_signed(mean)}{NBSP}p.e. ({pairs}, för få för ett intervall)"
    return f"{_signed(mean)}{NBSP}p.e. ({_signed(low)} till {_signed(high)}; {pairs})"


def _verdict(row: QuestionRow) -> str:
    if row.verdict is not None:
        return VERDICT_NAMES[row.verdict]
    return "Fel i körningen" if row.error else UNJUDGED_NAME


def _question_sources(row: QuestionRow) -> str:
    return f"{row.sources_found} av {row.document_sources}" if row.document_sources else "–"


def _calls(value: float | None) -> str:
    return "inte sparat" if value is None else plain_number(value)


def _signed(value: float) -> str:
    """A whole number with its sign: '+3', '−12', '+0'."""
    rounded = round(value)
    return ("−" if rounded < 0 else "+") + str(abs(rounded))


def _cost(dollars: tuple[float, float] | None) -> str:
    return "–" if dollars is None else dollar_range(*dollars)


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
