"""Tests for evals.answer_report: the Markdown and JSON reports of the answer evaluation.

The results are made up, scored with `evals.answer_scores.score`, so the
reports are tested without a model or a server.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from avtalsagent.agent.__main__ import CommandError
from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.schemas import Answer, Citation
from evals.answer_report import (
    AnswerReport,
    RunInfo,
    check_writable,
    overall_lines,
    render_markdown,
    report_json,
    report_stem,
    write_reports,
)
from evals.answer_run import ASK_USER_REPLY, QuestionRun, TokenUse
from evals.answer_scores import rule_judgement, score
from evals.answer_steps import Problem, Rejection, Step
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
    assert "Frågor som avtalen inte besvarar" not in markdown
    assert "Domaren svarade inte" not in markdown
    assert overall_lines(unjudged)[1] == "judge: none (--no-judge)"


def test_a_judge_that_gave_no_verdict_is_named_and_left_out_of_the_shares() -> None:
    plain = report()
    first, second, unanswered = plain.results
    failed = AnswerReport(
        **{
            **plain.__dict__,
            "results": (first, second, score(gold("q27", False), unanswered.run, None, None)),
        }
    )

    markdown = render_markdown(failed)

    assert "- **Domaren svarade inte** för 1 av 3 svar; varför står i loggen." in markdown
    assert "- **q27 Ej bedömd.** Domaren svarade inte; varför står i loggen." in markdown
    # q02's run failed, so the judge was never asked about it.
    assert "- **q02 Ej bedömd.** Körningen gav inget svar att bedöma." in markdown
    assert "Frågor som avtalen inte besvarar" not in markdown


def test_a_run_without_a_draft_is_counted_apart_from_no_answer() -> None:
    plain = report()
    no_draft = run(Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[]))
    results = (*plain.results, score(gold("q03"), no_draft, rule_judgement(no_draft), "rule"))

    markdown = render_markdown(AnswerReport(**{**plain.__dict__, "results": results}))

    assert "- **Utan svar inom gränsen för modellanrop:** 1 av 4 frågor (16 anrop)." in markdown
    assert "där agenten svarade att det inte framgår:** 0 av 3." in markdown
    assert "- **q03 Fel.** Agenten kom inte fram till ett svar" in markdown


def test_one_model_as_agent_and_reviewer_is_named_as_both() -> None:
    plain = report()
    same = AnswerReport(
        **{**plain.__dict__, "info": RunInfo(**{**INFO.__dict__, "reviewer_model": "gpt-6.1-sol"})}
    )

    assert "| gpt-6.1-sol | agent och granskare | 6 |" in render_markdown(same)


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
    with pytest.raises(CommandError, match="Rapporterna gick inte att skriva"):
        check_writable(blocked / "reports")


def test_the_check_before_the_run_makes_the_folder_and_leaves_it_empty(tmp_path: Path) -> None:
    check_writable(tmp_path / "reports")

    assert list((tmp_path / "reports").iterdir()) == []


def test_the_follow_up_line_is_written_also_when_the_agent_asked_nothing() -> None:
    markdown = render_markdown(report())

    assert "- **Följdfrågor:** agenten frågade användaren i 0 av 3 frågor." in markdown


# --- the agent's path -------------------------------------------------------------------------

TERMS = "e3" * 32
LONG_QUERY = "arbete på lördag och söndag och andra helgdagar inom bemanning"
STEPS = (
    Step("search_documents", {"query": LONG_QUERY, "framework_area": "Bemanning", "limit": 8}),
    Step("read_section", {"sha256": TERMS, "section_number": "9.9.2", "section_position": 80}),
    Step("read_section", {"sha256": TERMS, "section_position": 12}, target_from="reference"),
    Step("find_amendments", {"sha256": TERMS, "section_number": "9.9.2"}),
    Step(
        "read_section", {"sha256": SHA, "section_position": 3}, target_from="amendment", error=True
    ),
    Step("ask_user", {"question": "Vilket avtal?", "options": ["A", "B"]}),
    Step("FinalAnswer", {"text": "Ett."}, draft="sent_back"),
    Step("FinalAnswer", {"text": "Två."}, draft="refused"),
    Step("get_outline", {"sha256": TERMS}),
    Step(
        "calculate_date",
        {"start": "2027-02-17", "amount": 3, "unit": "months", "direction": "minus"},
    ),
    Step("no_such_tool", {"x": 1}, error=True),
    Step("FinalAnswer", {"text": "Tre."}, draft="submitted"),
)
REJECTED = Rejection(
    (
        Problem(
            "citations", "Källa [1]: citatet finns inte ordagrant i avsnitt 9.9.2. " + "x" * 300
        ),
        Problem("register_facts", "Datumet 2028-11-14 står inte i registret."),
    )
)


def with_path(plain: AnswerReport) -> AnswerReport:
    """`plain` with q01's steps, model calls and rejection saved, and q27's model calls."""
    first, second, third = plain.results
    tools = tuple(s.name for s in STEPS if s.name not in ("FinalAnswer", "ask_user"))
    q01 = replace(first.run, tools=tools, steps=STEPS, model_calls=16, rejections=(REJECTED,))
    q27 = replace(third.run, tools=(), model_calls=5, check_retries=0)
    return replace(
        plain,
        results=(replace(first, run=q01), second, replace(third, run=q27)),
    )


def test_each_questions_path_shows_its_calls_with_what_they_looked_for() -> None:
    markdown = render_markdown(with_path(report()))

    path = (
        "- **q01** (16 modellanrop; nya försök efter regel: citat 1, registeruppgifter 1): "
        'search\\_documents("arbete på lördag och söndag och andra h…", Bemanning) '
        "→ read\\_section(9.9.2) → ↪read\\_section(plats 12) → find\\_amendments(9.9.2) "
        '→ Δread\\_section(plats 3)✗ → ask\\_user("Vilket avtal?") '
        "→ FinalAnswer(underkänt: citat, registeruppgifter) → FinalAnswer(fel form) "
        f"→ get\\_outline({TERMS[:12]}…) → calculate\\_date(2027-02-17, minus, 3, months) "
        "→ no\\_such\\_tool✗ → FinalAnswer\n"
    )
    assert path in markdown
    # Each problem of each draft sent back, under the path and cut to 200 characters.
    reason = "  - Skäl till nytt försök 1, citat: Källa \\[1\\]: citatet finns inte ordagrant"
    (line,) = [line for line in markdown.splitlines() if line.startswith(reason)]
    assert line.endswith("x…") and len(line) < len(reason) + 200
    assert (
        "  - Skäl till nytt försök 1, registeruppgifter: Datumet 2028-11-14 står inte i "
        "registret." in markdown
    )
    assert "- **q27** (5 modellanrop): inga verktygsanrop" in markdown


def test_the_path_section_counts_the_tools_and_the_rejections_by_rule() -> None:
    markdown = render_markdown(with_path(report()))

    # q02's run kept only the names, and they count too.
    assert "| read\\_section | 4 | 2 |\n| search\\_documents | 2 | 2 |" in markdown
    assert "| no\\_such\\_tool | 1 | 1 |" in markdown
    assert "| citat | 1 | 1 | 1 |\n| registeruppgifter | 1 | 1 | 1 |" in markdown
    assert (
        "- **Nya försök:** kontrollen skickade tillbaka 1 av 3 svar minst en gång (1 nya försök); "
        "efter regel: citat 1, registeruppgifter 1." in markdown
    )
    assert (
        "- **Agentens modellanrop per fråga:** median 10,5, högst 16, mot gränsen 16 per "
        "körning; 1 av 2 frågor hade 16 eller fler (inte sparat för 1 fråga)." in markdown
    )
    assert (
        "- **Mål ur en hänvisning:** 1 av 3 anrop till `read_section` (33" in markdown
        and "i 1 av 1 frågor; 1 gick till en ändring som `find_amendments` angav "
        "(inte sparat för 1 fråga)."
        in markdown
    )


def test_a_run_that_saved_no_path_says_so_instead_of_counting_zero() -> None:
    markdown = render_markdown(report())  # runs made without the steps, as before

    assert "- **Agentens modellanrop per fråga:** inte sparat." in markdown
    assert "- **Mål ur en hänvisning:** inte sparat." in markdown
    assert "(2 nya försök); skälen inte sparade för 2 nya försök." in markdown
    assert "\nSkälen är inte sparade för 2 nya försök.\n\n### Vägen per fråga" in markdown
    assert "Kontrollen skickade inte tillbaka något utkast." not in markdown
    assert (
        "- **q01** (modellanropen inte sparade; skälen inte sparade för 1 nytt försök): "
        "search\\_documents → read\\_section (argumenten inte sparade)" in markdown
    )
    assert (
        "| q01 | enkel uppslagning | Rätt | Kontrollerat | 1 av 1 | 1 av 1 | – | 1 | 2 | – |"
        in (markdown)
    )


def test_a_run_without_new_attempts_says_so() -> None:
    plain = report()
    settled = [
        replace(r, run=replace(r.run, check_retries=0, model_calls=4)) for r in plain.results
    ]

    markdown = render_markdown(replace(plain, results=tuple(settled)))

    assert "svar minst en gång (0 nya försök).\n" in markdown
    assert "Kontrollen skickade inte tillbaka något utkast.\n\n### Vägen per fråga" in markdown
    assert "- **Agentens modellanrop per fråga:** median 4, högst 4, mot gränsen 16" in markdown


def test_the_run_names_the_commit_and_the_prompts_it_measured() -> None:
    measured = RunInfo(
        **{
            **INFO.__dict__,
            "commit": "44196de",
            "uncommitted": True,
            "system_prompt_sha256": "5a" * 32,
            "reviewer_prompt_sha256": "6b" * 32,
        }
    )
    markdown = render_markdown(replace(report(), info=measured))

    assert "- **Kod:** commit `44196de`, med ändringar som inte var incheckade" in markdown
    assert (
        "- **Prompter, sha256:** agentens systemprompt `5a5a5a5a5a5a`, granskarens prompt "
        "`6b6b6b6b6b6b`" in markdown
    )
    clean = render_markdown(replace(report(), info=replace(measured, uncommitted=False)))
    assert "- **Kod:** commit `44196de`\n" in clean
    unknown = render_markdown(report())  # without git, and a RunInfo made without them
    assert "- **Kod:** okänd commit (git gick inte att fråga där mätningen kördes)" in unknown
    assert (
        "- **Prompter, sha256:** agentens systemprompt inte sparat, granskarens prompt inte "
        "sparat" in unknown
    )


def test_the_json_holds_every_call_the_model_calls_and_the_reasons() -> None:
    data = json.loads(report_json(with_path(report())))

    first = data["questions"][0]
    assert first["model_calls"] == 16
    assert first["steps"][0] == {
        "name": "search_documents",
        "args": {"query": LONG_QUERY, "framework_area": "Bemanning", "limit": 8},
        "error": False,
        "draft": None,
        "target_from": None,
    }
    assert [step["target_from"] for step in first["steps"][1:5]] == [
        None,
        "reference",
        None,
        "amendment",
    ]
    assert first["rejections"][0][1] == {
        "rule": "register_facts",
        "text": "Datumet 2028-11-14 står inte i registret.",
    }
    assert data["questions"][1]["model_calls"] is None  # not saved
    path = data["path"]
    assert path["model_calls"] == {
        "median": 10.5,
        "max": 16,
        "limit": 16,
        "at_limit": 1,
        "missing": 1,
    }
    assert path["tools"]["read_section"] == {"calls": 4, "questions": 2}
    assert path["rejections"]["citations"] == {"drafts": 1, "questions": 1, "problems": 1}
    assert (path["reads"], path["reads_from_references"], path["reads_from_amendments"]) == (
        3,
        1,
        1,
    )
    assert data["run"]["commit"] is None and data["run"]["system_prompt_sha256"] is None


def test_the_printed_lines_have_the_path_in_english() -> None:
    lines = overall_lines(with_path(report()))

    assert lines[5] == (
        "model calls: median 10.5, max 16 (limit 16); read_section to a referenced target 1/3; "
        "retries by rule: citations 1, register_facts 1"
    )


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
