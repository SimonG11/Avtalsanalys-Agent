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
from evals.ask_judge import AskJudgement
from evals.gold import Alternative, DocumentSource, GoldQuestion, GoldScope
from evals.judge import Judgement
from evals.workflow_baseline import FIXED_STEPS

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
    """A run with an answer as the graph leaves it; without one, a session that never started."""
    if answer is None:  # as ask_question records it: nothing reached the graph
        return QuestionRun(
            answer=None,
            draft=None,
            tools=(),
            tool_errors=0,
            asked=(),
            check_retries=0,
            refused_drafts=0,
            seconds=seconds,
            usage={},
            error=error,
        )
    return QuestionRun(
        answer=answer,
        draft=None,
        tools=("search_documents", "read_section"),
        tool_errors=0,
        asked=(),
        check_retries=1,
        refused_drafts=0,
        seconds=seconds,
        usage={"gpt-6.1-sol": TokenUse(calls=3, input_tokens=100_000, cached_input_tokens=60_000)},
        error=error,
        steps=(
            Step("search_documents", {"query": "vite"}),
            Step("read_section", {"sha256": SHA, "section_position": 41}, found_by_search=True),
            Step("FinalAnswer", {"text": "Ett."}, draft="sent_back"),
            Step("FinalAnswer", {"text": "Två."}, draft="submitted"),
        ),
        model_calls=4,
        rejections=(Rejection((Problem("citations", "Källa [1]: citatet är för kort."),)),),
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
    assert "domarnas prompter" not in markdown
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

    assert (
        "- **Utan svar inom gränsen för modellanrop:** 1 av 4 frågor (16 anrop per körning)."
        in markdown
    )
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
    Step(
        "read_section",
        {"sha256": TERMS, "section_number": "9.9.2", "section_position": 80},
        found_by_search=True,
    ),
    Step(
        "resolve_reference",
        {"sha256": TERMS, "section_number": "9.9.2", "reference": "punkt 9.9.3"},
    ),
    # Two reads of a reference's target: one the search had returned too, one it had not.
    Step(
        "read_section",
        {"sha256": TERMS, "section_number": "9.9.3"},
        target_from="reference",
        found_by_search=True,
    ),
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
    """`plain` with q01's long path, and q27 answered at once; q02 never reached the graph."""
    first, second, third = plain.results
    tools = tuple(s.name for s in STEPS if s.name not in ("FinalAnswer", "ask_user"))
    q01 = replace(first.run, tools=tools, steps=STEPS, model_calls=16, rejections=(REJECTED,))
    q27 = replace(
        third.run,
        tools=(),
        steps=(Step("FinalAnswer", {"answered": False}, draft="submitted"),),
        model_calls=5,
        check_retries=0,
        rejections=(),
    )
    return replace(
        plain,
        results=(replace(first, run=q01), second, replace(third, run=q27)),
    )


def test_each_questions_path_shows_its_calls_with_what_they_looked_for() -> None:
    markdown = render_markdown(with_path(report()))

    path = (
        "- **q01** (16 modellanrop; nya försök efter regel: citat 1, registeruppgifter 1): "
        'search\\_documents("arbete på lördag och söndag och andra h…", Bemanning) '
        '→ read\\_section(9.9.2) → resolve\\_reference(9.9.2, "punkt 9.9.3") '
        "→ ↪read\\_section(9.9.3) → ↪read\\_section(plats 12) → find\\_amendments(9.9.2) "
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
    assert "- **q27** (5 modellanrop): FinalAnswer\n" in markdown
    assert "- **q02:** inte sparat (fel i körningen)\n" in markdown


def test_the_path_section_counts_the_tools_and_the_rejections_by_rule() -> None:
    markdown = render_markdown(with_path(report()))

    # Most calls first, then by name.
    assert "| read\\_section | 4 | 1 |\n| calculate\\_date | 1 | 1 |" in markdown
    assert "| resolve\\_reference | 1 | 1 |\n| search\\_documents | 1 | 1 |" in markdown
    assert "| citat | 1 | 1 | 1 |\n| registeruppgifter | 1 | 1 | 1 |" in markdown
    assert (
        "- **Nya försök:** kontrollen skickade tillbaka 1 av 3 svar minst en gång (1 nytt "
        "försök); efter regel, där ett utkast som flera regler underkände räknas under var och "
        "en: citat 1, registeruppgifter 1." in markdown
    )
    # q02 never reached the graph: it is named as not saved, in no number.
    assert (
        "- **Agentens modellanrop per fråga:** median 10,5, högst 16, mot gränsen 16 per "
        "körning; 1 av 2 frågor hade 16 eller fler (inte sparat för 1 fråga)." in markdown
    )
    assert (
        f"- **Mål ur en hänvisning:** 2 av 4 anrop till `read_section` (50{NBSP}%) gick till "
        "ett mål ur ett tidigare svars hänvisningar, i 1 av 2 frågor, och 1 av dem till ett mål "
        "som ingen tidigare sökning hade gett, i 1 av 2 frågor; 1 anrop gick till en ändring "
        "som `find_amendments` angav (inte sparat för 1 fråga)." in markdown
    )
    assert (
        "- **`resolve_reference`:** 1 anrop, i 1 av 2 frågor (inte sparat för 1 fråga)." in markdown
    )


def test_a_question_that_never_reached_the_graph_is_not_saved_rather_than_zero() -> None:
    plain = report()
    failed = plain.results[1]  # its session to avtal-mcp never started
    only_failed = replace(plain, results=(failed,))

    markdown = render_markdown(only_failed)

    assert "- **Agentens modellanrop per fråga:** inte sparat." in markdown
    assert "- **Mål ur en hänvisning:** inte sparat." in markdown
    assert "- **`resolve_reference`:** inte sparat." in markdown
    assert "- **q02:** inte sparat (fel i körningen)\n" in markdown
    assert (
        "| q02 | enkel uppslagning | Ej bedömd | Fel i körningen | – | 0 av 1 | – | 0 | 0 | – |"
        in (markdown)
    )
    data = json.loads(report_json(only_failed))
    assert data["path"]["model_calls"] == {
        "median": None,
        "max": None,
        "limit": 16,
        "at_limit": None,
    }
    assert (data["path"]["questions_saved"], data["path"]["not_saved"]) == (0, 1)
    question = data["questions"][0]
    assert (question["model_calls"], question["steps"], question["rejections"]) == (
        None,
        None,
        None,
    )
    assert overall_lines(only_failed)[5:7] == [
        "model calls: not saved (limit 16 per run)",
        "references: not saved",
    ]


def test_a_question_that_reached_the_graph_without_a_call_counts_as_none() -> None:
    plain = report()
    failed = plain.results[1]
    # The first model call failed: the state was read, and it holds no call and no step.
    stopped = replace(failed, run=replace(failed.run, error="ConnectionError: nej", model_calls=0))

    markdown = render_markdown(replace(plain, results=(stopped,)))

    assert "- **q02** (0 modellanrop): inga verktygsanrop\n" in markdown
    assert "- **Agentens modellanrop per fråga:** median 0, högst 0, mot gränsen 16" in markdown
    assert "- **`resolve_reference`:** 0 anrop, i 0 av 1 frågor." in markdown


def test_the_draft_that_became_an_answer_with_reservation_is_marked() -> None:
    plain = report()
    first = plain.results[0]
    assert first.run.answer is not None
    answer = first.run.answer.model_copy(update={"status": "with_reservation"})
    reserved = replace(first, run=replace(first.run, answer=answer))

    markdown = render_markdown(replace(plain, results=(reserved,)))

    assert "→ FinalAnswer(underkänt: citat) → FinalAnswer(med reservation)\n" in markdown
    # One the check passed is drawn plain.
    assert "→ FinalAnswer(underkänt: citat) → FinalAnswer\n" in render_markdown(plain)


def test_a_run_without_new_attempts_says_so() -> None:
    plain = report()
    settled = [
        replace(
            r,
            run=replace(
                r.run,
                check_retries=0,
                rejections=(),
                steps=tuple(step for step in r.run.steps if step.draft != "sent_back"),
            ),
        )
        for r in plain.results
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
            "commit_source": "git",
            "uncommitted": True,
            "system_prompt_sha256": "5a" * 32,
            "reviewer_prompt_sha256": "6b" * 32,
            "judge_prompt_sha256": "7c" * 32,
        }
    )
    markdown = render_markdown(replace(report(), info=measured))

    # The commit is the code of the agent and the measurement; avtal-mcp over HTTP may differ.
    assert (
        "- **Kod:** commit `44196de`, med ändringar som inte var incheckade; gäller agenten och "
        "mätningen, medan avtal-mcp på http://127.0.0.1:18011/mcp kan vara en annan version "
        "eller den tillfälliga ersättaren\n" in markdown
    )
    # The agent's prompt is hashed as the template, before the date is filled in.
    assert (
        "- **Prompter, sha256:** agentens systemprompt `5a5a5a5a5a5a` (mallen `SYSTEM_PROMPT`, "
        "innan dagens datum fylls i), granskarens prompt `6b6b6b6b6b6b`, domarnas prompter "
        "`7c7c7c7c7c7c` (`JUDGE_PROMPT` och `ASK_JUDGE_PROMPT`)\n" in markdown
    )
    assert (
        "- **Agent:** gpt-6.1-sol, resonemang low, högst 16 modellanrop per körning av grafen "
        "(en fråga där agenten frågar användaren är två körningar), högst 2 nya försök" in markdown
    )
    over_stdio = replace(measured, uncommitted=False, mcp="stdio")
    clean = render_markdown(replace(report(), info=over_stdio))
    assert (
        "- **Kod:** commit `44196de`; gäller agenten och mätningen, och avtal-mcp, som kördes "
        "som barnprocess ur samma installation\n" in clean
    )
    given = replace(measured, commit_source="environment", uncommitted=False)
    assert (
        "- **Kod:** commit `44196de` enligt `AVTALSAGENT_COMMIT` (git gick inte att fråga, så "
        "om koden hade ändringar som inte var incheckade syns inte); gäller agenten"
        in render_markdown(replace(report(), info=given))
    )
    unknown = render_markdown(report())  # neither git nor the variable told the commit
    assert (
        "- **Kod:** okänd commit (git gick inte att fråga där mätningen kördes, och "
        "`AVTALSAGENT_COMMIT` gav ingen commit: inte satt, eller inte en sha, se loggen); "
        "gäller agenten" in unknown
    )
    assert (
        "- **Prompter, sha256:** agentens systemprompt inte sparat (mallen `SYSTEM_PROMPT`, "
        "innan dagens datum fylls i), granskarens prompt inte sparat, domarnas prompter inte "
        "sparat (`JUDGE_PROMPT` och `ASK_JUDGE_PROMPT`)\n" in unknown
    )


def test_the_method_defines_the_questions_counted_and_when_the_reviewer_runs() -> None:
    markdown = render_markdown(report())

    assert "är därför de frågor vars väg sparades." in markdown
    assert "Det strängare måttet, ”ett mål som ingen tidigare sökning hade gett”" in markdown
    assert (
        "Granskaren körs bara när utkastet svarar på frågan (`answered` är sant) och de andra "
        "reglerna inte fann något fel" in markdown
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
        "found_by_search": False,
    }
    assert [(step["target_from"], step["found_by_search"]) for step in first["steps"][1:7]] == [
        (None, True),
        (None, False),
        ("reference", True),
        ("reference", False),
        (None, False),
        ("amendment", False),
    ]
    assert first["rejections"][0][1] == {
        "rule": "register_facts",
        "text": "Datumet 2028-11-14 står inte i registret.",
    }
    second = data["questions"][1]  # never reached the graph: not saved
    assert (second["model_calls"], second["steps"], second["rejections"]) == (None, None, None)
    path = data["path"]
    assert path["model_calls"] == {"median": 10.5, "max": 16, "limit": 16, "at_limit": 1}
    assert (path["questions_saved"], path["not_saved"]) == (2, 1)
    assert path["tools"]["read_section"] == {"calls": 4, "questions": 1}
    assert path["rejections"]["citations"] == {"drafts": 1, "questions": 1, "problems": 1}
    assert (path["reads"], path["reads_from_references"], path["reads_from_amendments"]) == (
        4,
        2,
        1,
    )
    assert path["reads_from_references_only"] == 1
    assert data["run"]["commit"] is None and data["run"]["commit_source"] is None
    assert data["run"]["system_prompt_sha256"] is None


def test_the_printed_lines_have_the_path_in_english() -> None:
    lines = overall_lines(with_path(report()))

    assert lines[5:8] == [
        "model calls: median 10.5, max 16 (limit 16 per run); not saved for 1 question",
        "references: read_section to a referenced target 2/4, of them not returned by an "
        "earlier search 1; resolve_reference calls 1; not saved for 1 question",
        "retries by rule (a draft under each rule that sent it back): citations 1, "
        "register_facts 1",
    ]


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


# --- the baseline and the questions to the user -----------------------------------------------

OPTIONS = ("Delområde 1", "Delområde 3")
SEPARATES = AskJudgement(separates=True, reason="Båda delområdena går att välja.")


def asking(id: str, should_ask: bool) -> GoldQuestion:
    return replace(gold(id), should_ask=should_ask, options=OPTIONS, clarification="Delområde 3.")


def asked(answer: Answer, *questions: str) -> QuestionRun:
    return replace(run(answer), asked=questions, asked_options=tuple(("1", "3") for _ in questions))


def ask_report(info: RunInfo = INFO) -> AnswerReport:
    answered = Answer(text="Minst 10 miljoner kronor [1].", status="verified", citations=[CITED])
    correct = Judgement(verdict="correct", missing=[], wrong=[], reason="Samma belopp.")
    results = (
        score(
            asking("a01", True),
            asked(answered, "Vilket delområde?"),
            correct,
            "judge",
            None,
            ask_judgement=SEPARATES,
        ),
        score(asking("a02", True), run(answered), None, None),
        score(asking("a05", False), asked(answered, "Större eller Mindre?"), correct, "judge"),
        score(asking("a06", False), run(answered), correct, "judge"),
        score(gold("q01"), run(answered), correct, "judge"),
    )
    return replace(report(), info=info, results=results)


def test_the_asks_section_shows_each_question_that_says_whether_to_ask() -> None:
    markdown = render_markdown(ask_report())

    summary = (
        "frågade när den borde i 1 av 2 frågor (1 med en motfråga som skiljer alternativen åt); "
        "frågade i onödan i 1 av 2 frågor"
    )
    assert f"- **Motfrågor:** {summary} (se Motfrågor)." in markdown
    assert f"## Motfrågor\n\nAgenten {summary}.\n" in markdown
    assert "| a01 | ja | ja | ja | rätt | Rätt |" in markdown
    assert "| a02 | ja | nej | – | fel | Ej bedömd |" in markdown
    assert "| a05 | nej | ja | – | onödig | Rätt |" in markdown
    assert "| a06 | nej | nej | – | rätt | Rätt |" in markdown
    assert "| q01 |" not in markdown.split("## Motfrågor")[1].split("##")[0]
    assert (
        "- **a01** (ska fråga; väntade alternativ: Delområde 1; Delområde 3). Frågade: "
        "”Vilket delområde?” Alternativ: 1; 3. Domaren: skiljer. Båda delområdena går att välja."
    ) in markdown
    assert "- **a02** (ska fråga; väntade alternativ: Delområde 1; Delområde 3). Frågade inte." in (
        markdown
    )
    assert "- **a05** (ska svara utan att fråga" in markdown
    assert "med frågans förtydligande när testsamlingen har ett" in markdown
    assert "- **Motfrågor:** för en fråga där testsamlingen säger" in markdown
    # The method follows the prompt's rule 4: ask when the cases are too many or depend on the
    # user's own case; answer each case when they are few and short.
    assert (
        "(för att fallen är för många för ett svar, eller för att svaret beror på uppgifter om "
        "användarens eget fall)"
    ) in markdown
    assert (
        "För en fråga där svaret är detsamma i alla alternativ, eller där fallen är få och "
        "korta och svaret ska ta upp vart och ett, är det rätt att inte fråga, och en motfråga "
        "räknas som onödig."
    ) in markdown
    # As judge_answer judges: the reply when the agent asked; the cases and the gold's case
    # (the judge's rule 7) when it should have asked and did not.
    assert (
        "Svaret bedöms mot facit som förut. När agenten frågade och mätningen svarade med "
        "förtydligandet, läser domaren det med frågan. När agenten inte frågade i en fråga där "
        "den skulle fråga, får domaren i stället veta vilka fall frågan passar och vilket fall "
        "facit bygger på (domarens regel 7). Ett svar som ger facits svar för det fallet och "
        "säger att det gäller det fallet har då kärnan, och svar för de andra fallen är inget "
        "fel. Att agenten inte frågade räknas alltså bara bland motfrågorna. Annars bedöms "
        "svaret mot frågan som den ställdes."
    ) in markdown


def test_a_question_whose_run_saved_nothing_is_not_counted() -> None:
    plain = ask_report()
    broken = run(None, error="avtal-mcp: ReadError")  # it does not show whether the agent asked
    lost = (
        score(asking("a03", True), broken, None, None),
        score(asking("a04", False), broken, None, None),
    )
    lossy = replace(plain, results=plain.results + lost)

    markdown = render_markdown(lossy)
    data = json.loads(report_json(lossy))

    # The same counts as without the two: an error is neither an ask nor a silence.
    assert data["asks"] == json.loads(report_json(plain))["asks"]
    assert "frågade när den borde i 1 av 2 frågor" in markdown
    assert "frågade i onödan i 1 av 2 frågor" in markdown
    assert "| a03 | ja | nej | – | räknas inte | Ej bedömd |" in markdown
    assert "| a04 | nej | nej | – | räknas inte | Ej bedömd |" in markdown
    assert "och räknas inte när inget av körningen sparades." in markdown
    lines = markdown.split("## Motfrågor")[1].split("\n")
    assert [line for line in lines if line.startswith(("- **a03**", "- **a04**"))] == [
        "- **a03** (ska fråga; väntade alternativ: Delområde 1; Delområde 3). Inget av "
        "körningen sparades, så frågan räknas inte.",
        "- **a04** (ska svara utan att fråga; väntade alternativ: Delområde 1; Delområde 3). "
        "Inget av körningen sparades, så frågan räknas inte.",
    ]
    assert (
        "En fråga där inget av körningen sparades (ett fel i sessionen mot avtal-mcp) räknas "
        "inte, varken bland frågorna där den skulle fråga eller bland dem där den inte skulle, "
        "eftersom det inte går att se om agenten frågade."
    ) in markdown
    # Neither a right silence nor an unnecessary ask in the JSON either.
    outcomes = [(item["asked_right"], item["unnecessary_ask"]) for item in data["questions"]]
    assert outcomes[-2:] == [(None, None), (None, None)]


def test_a_report_without_ask_questions_has_no_asks_section() -> None:
    markdown = render_markdown(report())

    assert "## Motfrågor" not in markdown and "- **Motfrågor:**" not in markdown
    assert json.loads(report_json(report()))["asks"] is None


def test_the_json_and_the_printout_carry_the_asks() -> None:
    data = json.loads(report_json(ask_report()))

    assert data["asks"] == {
        "should_ask": 2,
        "asked": 1,
        "separating": 1,
        "unjudged": 0,
        "should_not_ask": 2,
        "asked_unnecessarily": 1,
    }
    first = data["questions"][0]
    assert (first["should_ask"], first["asked_right"], first["unnecessary_ask"]) == (
        True,
        True,
        False,
    )
    assert first["asked_options"] == [["1", "3"]]
    assert first["expected_options"] == list(OPTIONS)
    assert first["ask_judgement"] == SEPARATES.model_dump()
    assert data["questions"][2]["unnecessary_ask"] is True
    assert data["questions"][4]["should_ask"] is None
    assert data["run"]["mode"] == "agent"
    assert (
        "asks: asked when it should 1/2 (separating 1, unjudged 0); asked unnecessarily 1/2"
        in overall_lines(ask_report())
    )


def test_a_workflow_run_is_named_lists_its_steps_and_asks_in_none() -> None:
    info = replace(INFO, mode="workflow")
    answered = Answer(text="Minst 10 miljoner kronor.", status="verified", citations=[])
    never = replace(
        ask_report(info),
        results=tuple(score(asking(f"a0{n}", True), run(answered), None, None) for n in (1, 2, 3)),
    )

    markdown = render_markdown(never)

    assert report_stem(never) == "answers-gpt-6.1-sol-low-workflow-stub"
    assert markdown.startswith(
        "# Mätning av baslinjens svar (fast arbetsflöde): gpt-6.1-sol, resonemang low\n"
    )
    assert "## Baslinjens fasta steg\n" in markdown
    assert f"1. {FIXED_STEPS[0]}" in markdown and f"6. {FIXED_STEPS[5]}" in markdown
    assert "- **Följdfrågor:** baslinjen kan inte fråga användaren." in markdown
    assert "frågade när den borde i 0 av 3 frågor (0 med en motfråga som skiljer" in markdown
    assert "frågade i onödan" not in markdown  # the gold here has no question of that kind
    assert "## Baslinjens väg" in markdown and "- **Baslinjens modellanrop per fråga:**" in markdown
    assert "Baslinjen frågade när den borde i 0 av 3 frågor" in markdown
    assert "- **Baslinje (fast arbetsflöde):** gpt-6.1-sol, resonemang low" in markdown
    assert "baslinjens prompter" in markdown and "`PLAN_PROMPT` med `QueryPlan`" in markdown
    assert "läsningen av frågan inräknad" in markdown
    assert overall_lines(never)[0].startswith("Answer evaluation, gpt-6.1-sol (low, workflow)")
    assert json.loads(report_json(never))["run"]["mode"] == "workflow"
