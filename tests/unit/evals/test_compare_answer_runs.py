"""Tests for evals.compare_answer_runs: two JSON reports compared on the same questions.

The reports are made by `answer_report.report_json` from made-up results, as
a run writes them, so the comparison reads the real format without a model.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from avtalsagent.agent.schemas import Answer
from evals.answer_report import AnswerReport, RunInfo, report_json
from evals.answer_run import QuestionRun, TokenUse
from evals.answer_scores import QuestionResult, score
from evals.compare_answer_runs import (
    CompareError,
    check_comparable,
    group_numbers,
    load_run,
    main,
    paired_difference,
    render_comparison,
)
from evals.gold import Alternative, DocumentSource, GoldQuestion, GoldScope
from evals.judge import Judgement, Verdict

SHA = "a1" * 32
NBSP = " "
INFO = RunInfo(
    agent_model="gpt-6.1-sol",
    agent_effort="low",
    model_call_limit=16,
    validation_retries=2,
    reviewer_model="gpt-6-astra",
    reviewer_effort="low",
    judge_model="gpt-6-astra",
    judge_effort="medium",
    mcp="stdio",
    concurrency=4,
    timeout=600.0,
    label=None,
)


def gold(id: str, category: str) -> GoldQuestion:
    alternative = Alternative(
        sha256=SHA,
        section_number="6.21.4",
        section_position=None,
        section_title="Vite",
        file_title="Allmänna villkor",
        page_title="IT-drift",
        pages=None,
        quote="vite",
    )
    return GoldQuestion(
        id=id,
        category=category,
        area="IT-drift",
        scope=GoldScope(),
        question=f"Fråga {id}?",
        answer="Svar.",
        answerable=True,
        sources=(DocumentSource((alternative,)),),
        why_hard="",
        difficulty=1,
    )


def result(id: str, category: str, verdict: Verdict | None, seconds: float) -> QuestionResult:
    run = QuestionRun(
        answer=Answer(text="Svar.", status="verified", citations=[]),
        draft=None,
        tools=("search_documents",),
        tool_errors=0,
        asked=(),
        check_retries=0,
        refused_drafts=0,
        seconds=seconds,
        usage={"gpt-6.1-sol": TokenUse(calls=2, input_tokens=10_000)},
        model_calls=2 if verdict != "incorrect" else 4,
    )
    judgement = (
        Judgement(verdict=verdict, missing=[], wrong=[], reason="Skäl.") if verdict else None
    )
    return score(gold(id, category), run, judgement, "judge" if judgement else None)


VERDICTS: list[tuple[str, str, Verdict | None, Verdict | None]] = [
    ("q01", "enkel", "correct", "correct"),
    ("q02", "enkel", "correct", "incorrect"),
    ("q03", "enkel", "partly_correct", "incorrect"),
    ("q04", "jämförelse", "correct", "partly_correct"),
    ("q05", "jämförelse", "correct", None),
]


def write(tmp_path: Path, name: str, report: AnswerReport) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(report_json(report), encoding="utf-8")
    return path


def reports(tmp_path: Path) -> tuple[Path, Path]:
    agent = tuple(result(id, c, a, 30.0) for id, c, a, _ in VERDICTS)
    workflow = tuple(result(id, c, b, 20.0) for id, c, _, b in VERDICTS)
    base = AnswerReport(
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
        gold_path="evals/datasets/gold_sv.jsonl",
        gold_sha256="7b4a" + "0" * 60,
        gold_questions=30,
        info=INFO,
        seconds=60.0,
        results=agent,
    )
    a = write(tmp_path, "answers-gpt-6.1-sol-low", base)
    b = write(
        tmp_path,
        "answers-gpt-6.1-sol-low-workflow",
        replace(base, info=replace(INFO, mode="workflow"), results=workflow),
    )
    return a, b


def test_two_reports_of_the_same_questions_are_compared_side_by_side(tmp_path: Path) -> None:
    a, b = (load_run(path) for path in reports(tmp_path))
    check_comparable(a, b)

    markdown = render_comparison(a, b)

    assert markdown.startswith(
        "# Jämförelse: agenten (gpt-6.1-sol, low) och baslinjen (gpt-6.1-sol, low)\n"
    )
    assert "- **B:** baslinjen (gpt-6.1-sol, low), `answers-gpt-6.1-sol-low-workflow.json`" in (
        markdown
    )
    assert f"| Rätt (poäng) | 4,5 av 5 (90{NBSP}%) | 1,5 av 4 (38{NBSP}%); 1 ej bedömda |" in (
        markdown
    )
    assert f"| −50{NBSP}p.e. (−88 till −12) |" in markdown  # paired over q01-q04
    assert f"| 0 av 5 (0{NBSP}%) | 0 av 5 (0{NBSP}%) | +0{NBSP}p.e. (+0 till +0) |" in markdown
    assert f"| Tid per fråga (median) | 30{NBSP}s | 20{NBSP}s | – |" in markdown
    assert "## Per kategori" in markdown and "### enkel (3 frågor)" in markdown
    assert "| q02 | enkel | Rätt | Fel | ≠ |" in markdown
    assert "| q01 | enkel | Rätt | Rätt |  |" in markdown
    assert "| q05 | jämförelse | Rätt | Ej bedömd | ≠ |" in markdown
    assert "Bedömningen skiljer sig i 4 av 5 frågor (≠)." in markdown
    assert "## Motfrågor" not in markdown


def test_the_paired_difference_counts_the_questions_judged_in_both(tmp_path: Path) -> None:
    a, b = (load_run(path) for path in reports(tmp_path))

    right = paired_difference(a.questions, b.questions, lambda row: row.score)

    # q01 0, q02 -1, q03 -0.5, q04 -0.5; q05 is unjudged in B.
    assert right is not None and right.questions == 4
    assert right.mean == pytest.approx(-0.5)
    assert right.low <= right.mean <= right.high
    assert paired_difference(a.questions, b.questions, lambda row: None) is None
    numbers = group_numbers(a.questions)
    assert (numbers.judged, numbers.right, numbers.median_model_calls) == (5, 4.5, 2)
    assert numbers.cost is not None and numbers.cost[0] == pytest.approx(5 * 10_000 * 2.0 / 1e6)


def test_reports_of_another_gold_file_or_other_questions_are_refused(tmp_path: Path) -> None:
    a, b = (load_run(path) for path in reports(tmp_path))

    with pytest.raises(CompareError, match="different gold files"):
        check_comparable(a, replace(b, gold_sha256="f" * 64))
    with pytest.raises(CompareError, match=r"only in A: q05; only in B: none"):
        check_comparable(a, replace(b, questions=b.questions[:4]))


def test_the_command_writes_the_comparison_or_stops_with_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = reports(tmp_path)
    out = tmp_path / "out"

    main([str(a), str(b), "--out", str(out)])

    written = out / "compare-answers-gpt-6.1-sol-low-vs-answers-gpt-6.1-sol-low-workflow.md"
    assert written.exists() and capsys.readouterr().out == f"Comparison: {written}\n"
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as end:
        main([str(a), str(broken), "--out", str(out)])
    assert end.value.code == 1
    assert "is not a report of run_answer_eval" in capsys.readouterr().err


def test_the_asks_numbers_are_compared_when_a_report_has_them(tmp_path: Path) -> None:
    a, b = (load_run(path) for path in reports(tmp_path))
    asks = {
        "should_ask": 4,
        "asked": 3,
        "separating": 2,
        "unjudged": 0,
        "should_not_ask": 3,
        "asked_unnecessarily": 1,
    }
    never = asks | {"asked": 0, "separating": 0, "asked_unnecessarily": 0}

    markdown = render_comparison(replace(a, asks=asks), replace(b, asks=never))

    assert "| Frågade när den borde | 3 av 4 (2 skiljer) | 0 av 4 (0 skiljer) |" in markdown
    assert "| Frågade i onödan | 1 av 3 | 0 av 3 |" in markdown


def test_a_report_made_before_the_mode_was_recorded_is_the_agents(tmp_path: Path) -> None:
    a, _ = reports(tmp_path)
    text = a.read_text(encoding="utf-8")
    assert '"mode": "agent",' in text
    a.write_text(text.replace('"mode": "agent",', ""), encoding="utf-8")

    assert load_run(a).mode == "agent"
