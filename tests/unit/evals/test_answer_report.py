"""Tests for evals.answer_report: the Markdown and JSON reports of the answer evaluation.

The results are made up, scored with `evals.answer_scores.score`, so the
reports are tested without a model or a server.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from avtalsagent.agent.__main__ import CommandError
from avtalsagent.agent.schemas import Answer, Citation
from evals.answer_report import (
    AnswerReport,
    RunInfo,
    overall_lines,
    render_markdown,
    report_json,
    report_stem,
    write_reports,
)
from evals.answer_run import ASK_USER_REPLY, QuestionRun, TokenUse
from evals.answer_scores import score
from evals.gold import Alternative, DocumentSource, GoldQuestion, GoldScope
from evals.judge import Judgement

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
    mcp="http://127.0.0.1:18011/mcp",
    concurrency=4,
    timeout=600.0,
    label="stub",
)


def gold(id: str, answerable: bool = True) -> GoldQuestion:
    source = DocumentSource(
        (
            Alternative(
                sha256=SHA,
                section_number="6.21.4",
                section_position=None,
                section_title="Vite",
                file_title="Allmänna villkor",
                page_title="IT-drift",
                pages=None,
                quote="vite",
            ),
        )
    )
    return GoldQuestion(
        id=id,
        category="enkel uppslagning" if answerable else "fråga utan svar i avtalen",
        area="IT-drift",
        scope=GoldScope(),
        question="Hur stort är vitet | per dag?",
        answer="5 000 kronor *per* dag.",
        answerable=answerable,
        sources=(source,) if answerable else (),
        why_hard="",
        difficulty=1,
    )


def run(answer: Answer | None, error: str | None = None, seconds: float = 12.0) -> QuestionRun:
    return QuestionRun(
        answer=answer,
        draft=None,
        tools=("search_documents", "read_section"),
        tool_errors=0,
        asked=(),
        check_retries=1 if answer else 0,
        refused_drafts=0,
        seconds=seconds,
        usage={"gpt-6.1-sol": TokenUse(calls=3, input_tokens=100_000, cached_input_tokens=60_000)}
        if answer
        else {},
        error=error,
    )


CITED = Citation(
    id=1,
    sha256=SHA,
    file_title="Allmänna villkor",
    page_title=None,
    section_number="6.21.4",
    section_title="Vite",
    page=3,
    quote="vite om 5 000 kronor",
    verified=True,
)


def report() -> AnswerReport:
    answered = Answer(
        text="Vitet är 5 000 kronor per dag [1].\n\n- Gäller per påbörjad dag.",
        status="verified",
        citations=[CITED],
    )
    results = (
        score(
            gold("q01"),
            run(answered),
            Judgement(verdict="correct", missing=[], wrong=[], reason="Samma belopp som facit."),
            "judge",
            {"gpt-6-astra": TokenUse(calls=1, input_tokens=2_000, output_tokens=100)},
        ),
        score(gold("q02"), run(None, error="avtal-mcp: ConnectError: nej", seconds=3), None, None),
        score(
            gold("q27", answerable=False),
            run(Answer(text="Framgår inte.", status="no_answer", citations=[])),
            Judgement(
                verdict="correct", missing=[], wrong=[], reason="Säger att det inte framgår."
            ),
            "judge",
        ),
    )
    return AnswerReport(
        created_at=datetime(2026, 10, 7, 9, 50, tzinfo=UTC),
        gold_path="evals/datasets/gold_sv.jsonl",
        gold_sha256="7b4a28130660" + "0" * 52,
        gold_questions=30,
        info=INFO,
        seconds=95.0,
        results=results,
    )


def test_the_stem_names_the_model_the_effort_and_the_label() -> None:
    assert report_stem(report()) == "answers-gpt-6.1-sol-low-stub"
    odd = AnswerReport(
        **{
            **report().__dict__,
            "info": RunInfo(**{**INFO.__dict__, "label": "../riktig databas"}),
        }
    )
    assert report_stem(odd) == "answers-gpt-6.1-sol-low-.._riktig_databas"


def test_the_markdown_leads_with_the_numbers_in_swedish() -> None:
    markdown = render_markdown(report())

    assert markdown.startswith("# Mätning av agentens svar: gpt-6.1-sol, resonemang low\n")
    assert (
        f"- **Rätt enligt domaren:** 2 av 3 (67{NBSP}%); delvis rätt 0, fel 0, ej bedömda 1."
        in (markdown)
    )
    assert (
        "- **Status:** Kontrollerat 1, Med reservation 0, Inget svar 1; fel i körningen 1."
        in markdown
    )
    assert "- **Frågor som avtalen inte besvarar:** 1 av 1 fick ett rätt ”framgår inte”." in (
        markdown
    )
    assert "- **Facits källor citerade:** 1 av 2 (50" in markdown
    # 2 × 100 000 tokens of input, 120 000 of them cached, at 2 dollars per million.
    assert f"- **Kostnad:** 0,16–0,40{NBSP}USD för agenten och granskaren" in markdown
    assert "- **Domare:** gpt-6-astra, resonemang medium" in markdown
    assert ASK_USER_REPLY in markdown


def test_the_markdown_has_each_question_its_error_and_its_answer() -> None:
    markdown = render_markdown(report())

    assert "| q01 | enkel uppslagning | Rätt | Kontrollerat | 1 av 1 | 1 av 1 | – | 1 | 2 |" in (
        markdown
    )
    assert "| q02 | enkel uppslagning | Ej bedömd | Fel i körningen | – | 0 av 1 |" in markdown
    assert "- **q02:** avtal-mcp: ConnectError: nej" in markdown
    assert "- **q01 Rätt.** Samma belopp som facit." in markdown
    # Text in a table or a line is escaped; an answer keeps its lines in a block quote.
    assert "**Fråga:** Hur stort är vitet \\| per dag?" in markdown
    assert "**Facit:** 5 000 kronor \\*per\\* dag." in markdown
    assert "> Vitet är 5 000 kronor per dag \\[1\\].\n>\n> - Gäller per påbörjad dag." in markdown
    assert "- [1] Allmänna villkor, 6.21.4 Vite ✓" in markdown
    assert "**Svar:** inget (fel i körningen)." in markdown


def test_the_markdown_without_a_judge_says_so() -> None:
    plain = report()
    unjudged = AnswerReport(
        **{
            **plain.__dict__,
            "info": RunInfo(**{**INFO.__dict__, "judge_model": None, "judge_effort": None}),
            "results": tuple(
                score(gold(r.id, r.answerable), r.run, None, None) for r in plain.results
            ),
        }
    )

    markdown = render_markdown(unjudged)

    assert "- **Rätt enligt domaren:** ingen bedömning (körd utan domare)." in markdown
    assert "- **Domare:** ingen (--no-judge)" in markdown


def test_the_json_holds_the_run_the_summary_and_every_answer() -> None:
    data = json.loads(report_json(report()))

    assert data["run"]["agent_model"] == "gpt-6.1-sol" and data["run"]["seconds"] == 95.0
    assert data["gold"] == {
        "path": "evals/datasets/gold_sv.jsonl",
        "sha256": "7b4a28130660" + "0" * 52,
        "questions": 30,
    }
    summary = data["summary"]
    assert summary["verdicts"] == {
        "correct": 2,
        "partly_correct": 0,
        "incorrect": 0,
        "unjudged": 1,
    }
    assert summary["seconds"] == {"median": 12.0, "p90": 12.0, "max": 12.0}
    assert summary["cost"] == {"low": 0.16, "high": 0.4}
    assert data["judge"]["gpt-6-astra"]["calls"] == 1
    assert list(data["by_category"]) == ["enkel uppslagning", "fråga utan svar i avtalen"]
    first, second, _ = data["questions"]
    assert first["verdict"] == "correct" and first["answer"]["citations"][0]["verified"]
    assert first["sources_found"] == [True] and first["cost"] == {"low": 0.08, "high": 0.2}
    assert second["error"] == "avtal-mcp: ConnectError: nej" and second["answer"] is None


def test_the_overall_lines_are_printed_in_english() -> None:
    lines = overall_lines(report())

    assert lines[0] == "Answer evaluation, gpt-6.1-sol (low): 3 questions in 95 s"
    assert lines[1] == "judge: correct 2, partly 0, incorrect 0, unjudged 1"
    assert lines[2] == "status: verified 1, with_reservation 0, no_answer 1, error 1"
    assert lines[-1] == "cost: $0.16-0.40 (agent and reviewer)"


def test_the_reports_are_written_next_to_each_other(tmp_path: Path) -> None:
    markdown, data = write_reports(report(), tmp_path / "reports")

    assert markdown == tmp_path / "reports" / "answers-gpt-6.1-sol-low-stub.md"
    assert data.with_suffix(".md") == markdown
    assert markdown.read_text(encoding="utf-8").startswith("# Mätning av agentens svar")
    assert json.loads(data.read_text(encoding="utf-8"))["run"]["label"] == "stub"


def test_a_report_that_cannot_be_written_stops_with_a_clear_error(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("")

    with pytest.raises(CommandError, match="Rapporterna gick inte att skriva"):
        write_reports(report(), blocked / "reports")


def test_what_is_missing_and_wrong_is_one_sentence_each() -> None:
    partly = Judgement(
        verdict="partly_correct",
        missing=["Att priset är exklusive moms."],
        wrong=["Sju anbud.", "Fel datum"],
        reason="Rätt pris men utan moms.",
    )
    plain = report()
    result = score(gold("q18"), plain.results[0].run, partly, "judge")
    markdown = render_markdown(AnswerReport(**{**plain.__dict__, "results": (result,)}))

    assert (
        "- **q18 Delvis rätt.** Rätt pris men utan moms. Saknas: Att priset är exklusive moms. "
        "Motsäger facit: Sju anbud; Fel datum." in markdown
    )
