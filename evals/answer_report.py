"""The answer evaluation's reports: Markdown in Swedish, and JSON (M11).

What:
    `AnswerReport` holds a run: when, on which gold file, how (`RunInfo`:
    the models, the bounds, and the commit and prompts measured) and every
    `QuestionResult`. `render_markdown` writes the Swedish report: the
    summary (with the new attempts by rule, the follow-ups, the model calls
    against the limit, the reads of a reference's target and the calls to
    `resolve_reference`), the run, the method, a table per category and per
    question, the agent's path (calls per tool, the check's rejections by
    rule, and a line per question), the tokens and cost by model, the
    judge's reason for each verdict, and each answer next to the gold
    answer. `report_json` holds the same, with each answer as the web app
    gets it and every tool call with its arguments; `overall_lines` is what
    the command prints, and `write_reports` writes both files. A run of
    the baseline (`RunInfo.mode` "workflow", ADR 0024) is named so in the
    file names, the title and the run, and its report lists the fixed
    steps. A gold file with questions that say whether the agent should ask
    the user gets a section "Motfrågor": per question whether it should ask,
    whether it asked, its question and options, whether the question
    separates the gold's options and why, and the verdict, with a summary
    line; the JSON has the same per question and as `asks`.

Why:
    The Markdown is what is shown and read: the numbers first, then what it
    takes to check them, the judge's reasons and the answers themselves. The
    JSON is for comparing runs. Unlike the search's reports, these hold the
    questions, the answers and their quotes, since a verdict cannot be
    checked without them; git ignores evals/reports/, so they stay on the
    machine that ran them.

How:
    Pure functions over the results, `summarize` and `summarize_paths`.
    Numbers are written the Swedish way (a decimal comma, a no-break space
    before "%"), and text in a table is put on one line with markup
    characters escaped. The cost is a range: cached input at no cost, and at
    the full input price. A question's path shows each call with the
    arguments that say what it looked for (`_SHOWN_ARGS`), each cut to
    ARG_CHARS; a file's hash is shown by its first 12 characters, and a
    limit or an offset is only in the JSON. The draft that became an answer
    with reservation is marked as such, so it is not taken for one the
    check passed. What a run did not save (one that never reached the
    graph: its path, model calls and rejections) is named as not saved
    ("inte sparat"), null in the JSON, and left out of the path's numbers,
    never counted as zero. A baseline never asks, so it asked when it
    should in 0 of the questions that should ask.
"""

import json
import re
import statistics
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from avtalsagent.agent.__main__ import STATUS_NAMES, CommandError
from avtalsagent.agent.middleware import FINAL_ANSWER_TOOL
from evals.answer_run import (
    ASK_USER_REPLY,
    PRICES,
    QuestionRun,
    TokenUse,
    cost_range,
    total_use,
)
from evals.answer_scores import (
    STATUSES,
    AskSummary,
    PathSummary,
    QuestionResult,
    Summary,
    ToolCount,
    by_category,
    median,
    percentile,
    summarize,
    summarize_asks,
    summarize_paths,
)
from evals.answer_steps import RESOLVE_REFERENCE, CheckRule, Rejection, Step, rule_counts
from evals.workflow_baseline import FIXED_STEPS

VERDICT_NAMES = {"correct": "Rätt", "partly_correct": "Delvis rätt", "incorrect": "Fel"}
UNJUDGED_NAME = "Ej bedömd"
RULE_NAMES: dict[CheckRule, str] = {
    "citations": "citat",
    "register_facts": "registeruppgifter",
    "latest_wording": "senaste lydelsen",
    "review": "granskaren",
    "unknown": "okänd regel",
}
NOT_SAVED = "inte sparat"
# Where the commit measured came from: git, or the variable set where git cannot tell.
CommitSource = Literal["git", "environment"]
COMMIT_VARIABLE = "AVTALSAGENT_COMMIT"
# What was asked: the agent, or the baseline's fixed workflow (ADR 0024).
Mode = Literal["agent", "workflow"]
# How much of an argument a path shows, and of a problem the check found.
ARG_CHARS = 40
PROBLEM_CHARS = 200


@dataclass(frozen=True)
class RunInfo:
    """How the measurement ran: the models, the bounds, the server and the gold."""

    agent_model: str
    agent_effort: str
    model_call_limit: int
    validation_retries: int
    reviewer_model: str
    reviewer_effort: str
    judge_model: str | None  # None without a judge (--no-judge)
    judge_effort: str | None
    mcp: str  # "stdio" or the server's address
    concurrency: int
    timeout: float
    label: str | None
    # What was measured; None when it could not be told, or was not given.
    commit: str | None = None  # the short sha of the agent's and the measurement's code
    commit_source: CommitSource | None = None
    uncommitted: bool = False  # by git: its working tree had changes that were not committed
    # Of SYSTEM_PROMPT, or of the baseline's prompt in workflow mode, before the date is in.
    system_prompt_sha256: str | None = None
    reviewer_prompt_sha256: str | None = None  # of REVIEWER_PROMPT
    mode: Mode = "agent"


@dataclass(frozen=True)
class AnswerReport:
    created_at: datetime
    gold_path: str
    gold_sha256: str
    gold_questions: int  # in the gold file; results may hold fewer (--only)
    info: RunInfo
    seconds: float  # the whole run
    results: tuple[QuestionResult, ...]


def report_stem(report: AnswerReport) -> str:
    """`answers-<agent model>-<effort>[-workflow][-<label>]`, safe as a file name."""
    parts = ["answers", report.info.agent_model, report.info.agent_effort]
    if report.info.mode == "workflow":
        parts.append("workflow")
    if report.info.label:
        parts.append(report.info.label)
    return "-".join(re.sub(r"[^A-Za-z0-9.-]+", "_", part).strip("_") or "_" for part in parts)


def report_json(report: AnswerReport) -> str:
    """The JSON report: the run, the summary and every question with its answer."""
    summary = summarize(report.results)
    data = {
        "created_at": report.created_at.isoformat(),
        "gold": {
            "path": report.gold_path,
            "sha256": report.gold_sha256,
            "questions": report.gold_questions,
        },
        "run": asdict(report.info) | {"seconds": round(report.seconds, 1)},
        "summary": _summary_json(summary),
        "judge": _usage_json(total_use(r.judge_usage for r in report.results)),
        "by_category": {
            category: _summary_json(summarize(group))
            for category, group in by_category(report.results).items()
        },
        "path": _path_json(summarize_paths(report.results), report.info.model_call_limit),
        "asks": _asks_json(summarize_asks(report.results)),
        "questions": [_result_json(result) for result in report.results],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _path_json(paths: PathSummary, limit: int) -> dict[str, Any]:
    """The path's numbers; the model calls' are null when no question saved them."""
    data = asdict(paths)
    calls = paths.model_calls
    data["model_calls"] = {
        "median": median(calls) if calls else None,
        "max": max(calls) if calls else None,
        "limit": limit,  # per run of the graph
        "at_limit": sum(1 for count in calls if count >= limit) if calls else None,
    }
    return data


def _asks_json(asks: AskSummary | None) -> dict[str, Any] | None:
    return asdict(asks) if asks is not None else None


def _summary_json(summary: Summary) -> dict[str, Any]:
    data = asdict(summary)
    data["seconds"] = {
        "median": round(median(summary.seconds), 1),
        "p90": round(percentile(summary.seconds, 0.9), 1),
        "max": round(max(summary.seconds, default=0.0), 1),
    }
    data["tool_calls"] = {
        "mean": round(statistics.fmean(summary.tool_calls), 2) if summary.tool_calls else 0.0,
        "max": max(summary.tool_calls, default=0),
    }
    data["usage"] = _usage_json(summary.usage)
    data["cost"] = _range_json(summary.cost)
    return data


def _usage_json(usage: Mapping[str, TokenUse]) -> dict[str, Any]:
    return {
        model: asdict(tokens) | {"cost": _range_json(cost_range({model: tokens}))}
        for model, tokens in usage.items()
    }


def _range_json(dollars: tuple[float, float] | None) -> dict[str, float] | None:
    """A cost in dollars, low and high (`cost_range`)."""
    if dollars is None:
        return None
    low, high = dollars
    return {"low": round(low, 4), "high": round(high, 4)}


def _result_json(result: QuestionResult) -> dict[str, Any]:
    run = result.run
    return {
        "id": result.id,
        "category": result.category,
        "answerable": result.answerable,
        "difficulty": result.difficulty,
        "question": result.question,
        "gold_answer": result.gold_answer,
        "status": result.status,
        "error": run.error,
        "verdict": result.verdict,
        "judged_by": result.judged_by,
        "judgement": result.judgement.model_dump() if result.judgement else None,
        "citations": result.citations,
        "verified_citations": result.verified_citations,
        "cited": [asdict(place) for place in result.places],
        "document_sources": result.document_sources,
        "sources_found": list(result.sources_found),
        "register": {
            "required": result.register_required,
            "found": result.register_found,
            "extra": result.register_extra,
        },
        "check_retries": run.check_retries,
        "rejections": [[asdict(problem) for problem in r.problems] for r in run.rejections]
        if run.path_saved
        else None,
        "refused_drafts": run.refused_drafts,
        "tools": list(run.tools),
        "tool_errors": run.tool_errors,
        "model_calls": run.model_calls,
        "steps": [asdict(step) for step in run.steps] if run.path_saved else None,
        "asked": list(run.asked),
        "asked_options": [list(options) for options in run.asked_options],
        "should_ask": result.should_ask,
        "expected_options": list(result.expected_options),
        "ask_judgement": result.ask_judgement.model_dump() if result.ask_judgement else None,
        "asked_right": result.asked_right,
        "unnecessary_ask": result.unnecessary_ask,
        "seconds": round(run.seconds, 1),
        "usage": _usage_json(run.usage),
        "cost": _range_json(result.cost),
        "judge_usage": _usage_json(result.judge_usage),
        "answer": run.answer.model_dump(mode="json") if run.answer else None,
    }


_NBSP = "\u00a0"  # between thousands and before "%": "13 175", "95 %"
_MARKUP = str.maketrans({char: "\\" + char for char in "\\`*_[]<>|"})
_UNJUDGED = "unjudged"


def render_markdown(report: AnswerReport) -> str:
    """The Markdown report, in Swedish."""
    info = report.info
    model = f"{_md(info.agent_model)}, resonemang {_md(info.agent_effort)}"
    if info.mode == "workflow":
        lines = [
            f"# Mätning av baslinjens svar (fast arbetsflöde): {model}",
            "",
            "Guldfrågorna ställs till baslinjen: ett fast arbetsflöde med agentens modell, "
            "kontroll, granskare och gränser, men utan agentens val av steg (ADR 0024). En "
            "domare (en språkmodell) jämför varje svar med facit, och svarets källor jämförs "
            "med facits.",
            "",
        ]
    else:
        lines = [
            f"# Mätning av agentens svar: {model}",
            "",
            "Guldfrågorna ställs till agenten med samma graf, kontroll och granskare som API:t "
            "kör. En domare (en språkmodell) jämför varje svar med facit, och svarets källor "
            "jämförs med facits.",
            "",
        ]
    lines += _md_summary(report)
    lines += _md_run(report)
    if info.mode == "workflow":
        lines += _md_fixed_steps()
    lines += _md_method(report)
    lines += _md_categories(report.results)
    lines += _md_questions(report.results)
    lines += _md_asks(report)
    lines += _md_paths(report)
    lines += _md_usage(report)
    lines += _md_judgements(report)
    lines += _md_answers(report.results)
    return "\n".join(lines).rstrip("\n") + "\n"


def _md_summary(report: AnswerReport) -> list[str]:
    s = summarize(report.results)
    n = s.questions
    v = s.verdicts
    lines = ["## Sammanfattning", ""]
    if report.info.judge_model is None:
        lines.append("- **Rätt enligt domaren:** ingen bedömning (körd utan domare).")
    else:
        line = (
            f"- **Rätt enligt domaren:** {v['correct']} av {n} ({_percent(v['correct'], n)}); "
            f"delvis rätt {v['partly_correct']}, fel {v['incorrect']}"
        )
        if v[_UNJUDGED]:
            line += f", ej bedömda {v[_UNJUDGED]}"
        lines.append(line + ".")
        if failed := sum(1 for r in report.results if _judge_failed(r)):
            lines.append(
                f"- **Domaren svarade inte** för {failed} av {n} svar; varför står i loggen."
            )
    statuses = ", ".join(f"{STATUS_NAMES[status]} {s.statuses[status]}" for status in STATUSES)
    lines.append(f"- **Status:** {statuses}; fel i körningen {s.statuses['error']}.")
    if s.unanswerable_judged:
        line = (
            f"- **Frågor som avtalen inte besvarar:** {s.unanswerable_correct} av "
            f"{s.unanswerable_judged} fick ett rätt ”framgår inte”"
        )
        if unjudged := s.unanswerable - s.unanswerable_judged:
            line += f" ({unjudged} ej bedömda)"
        lines.append(line + ".")
    if s.answerable:
        lines.append(
            f"- **Frågor som avtalen besvarar men där agenten svarade att det inte framgår:** "
            f"{s.answerable_no_answer} av {s.answerable}."
        )
    if s.no_draft:
        lines.append(
            f"- **Utan svar inom gränsen för modellanrop:** {s.no_draft} av {n} frågor "
            f"({report.info.model_call_limit} anrop per körning)."
        )
    if s.citations:
        lines.append(
            f"- **Citat:** {s.verified_citations} av {s.citations} står ordagrant i sitt avsnitt "
            f"({_percent(s.verified_citations, s.citations)}); alla citat är ordagranna i "
            f"{s.answers_all_verified} av {s.answers_with_citations} svar med citat."
        )
    if s.document_sources:
        lines.append(
            f"- **Facits källor citerade:** {s.sources_found} av {s.document_sources} "
            f"({_percent(s.sources_found, s.document_sources)}); alla källor i "
            f"{s.questions_all_sources} av {s.questions_with_sources} frågor."
        )
    if s.register_required:
        lines.append(
            f"- **Registeruppgifter:** {s.register_found} av {s.register_required} avtal ur facit "
            f"finns bland svarens registerrader; {s.register_extra} avtal utöver facit."
        )
    paths = summarize_paths(report.results)
    line = (
        f"- **Nya försök:** kontrollen skickade tillbaka {s.retried} av {n} svar minst en gång "
        f"({_attempts(s.retries)})"
    )
    if paths.rejections:
        line += (
            "; efter regel, där ett utkast som flera regler underkände räknas under var och en: "
            + ", ".join(
                f"{RULE_NAMES[rule]} {count.drafts}" for rule, count in paths.rejections.items()
            )
        )
    lines.append(line + ".")
    if report.info.mode == "workflow":
        lines.append("- **Följdfrågor:** baslinjen kan inte fråga användaren.")
    else:
        lines.append(f"- **Följdfrågor:** agenten frågade användaren i {s.asked} av {n} frågor.")
    if (asks := summarize_asks(report.results)) is not None:
        lines.append(f"- **Motfrågor:** {_asks_line(asks)} (se Motfrågor).")
    lines.append(
        f"- **{_whose(report.info)} modellanrop per fråga:** {_model_calls(paths, report.info)}."
    )
    lines.append(f"- **Mål ur en hänvisning:** {_reads(paths)}.")
    lines.append(f"- **`resolve_reference`:** {_resolved(paths)}.")
    lines.append(
        f"- **Tid per fråga:** median {_seconds(median(s.seconds))}, 90:e percentilen "
        f"{_seconds(percentile(s.seconds, 0.9))}, längst {_seconds(max(s.seconds, default=0.0))}."
    )
    if s.cost is not None and n:
        low, high = s.cost
        lines.append(
            f"- **Kostnad:** {_dollars_range(low, high)} för agenten och granskaren, "
            f"{_dollars_range(low / n, high / n)} per fråga (se Tokens och kostnad)."
        )
    return [*lines, ""]


def _md_run(report: AnswerReport) -> list[str]:
    info = report.info
    judge = (
        f"{_md(info.judge_model)}, resonemang {_md(info.judge_effort or '')}"
        if info.judge_model
        else "ingen (--no-judge)"
    )
    if info.mode == "workflow":
        who = (
            f"- **Baslinje (fast arbetsflöde):** {_md(info.agent_model)}, resonemang "
            f"{_md(info.agent_effort)}, högst {info.model_call_limit} modellanrop per fråga "
            f"(läsningen av frågan inräknad), högst {info.validation_retries} nya försök"
        )
        prompt = (
            f"baslinjens systemprompt {_sha(info.system_prompt_sha256)} (`workflow_template`, "
            "byggd av delar av agentens `SYSTEM_PROMPT`, innan dagens datum fylls i)"
        )
    else:
        who = (
            f"- **Agent:** {_md(info.agent_model)}, resonemang {_md(info.agent_effort)}, högst "
            f"{info.model_call_limit} modellanrop per körning av grafen (en fråga där agenten "
            f"frågar användaren är två körningar), högst {info.validation_retries} nya försök"
        )
        prompt = (
            f"agentens systemprompt {_sha(info.system_prompt_sha256)} (mallen `SYSTEM_PROMPT`, "
            "innan dagens datum fylls i)"
        )
    return [
        "## Körning",
        "",
        f"- **Tid:** {_moment(report.created_at)}, {_seconds(report.seconds)} totalt",
        who,
        f"- **Granskare:** {_md(info.reviewer_model)}, resonemang {_md(info.reviewer_effort)}",
        f"- **Domare:** {judge}",
        f"- **avtal-mcp:** {_md(info.mcp)}",
        f"- **Guldfil:** {_md(report.gold_path)} (sha256 `{report.gold_sha256[:12]}`), "
        f"{len(report.results)} av {report.gold_questions} frågor",
        f"- **Samtidiga frågor:** {info.concurrency}; tidsgräns {_seconds(info.timeout)} per fråga",
        f"- **Kod:** {_commit(info)}",
        f"- **Prompter, sha256:** {prompt}, granskarens prompt {_sha(info.reviewer_prompt_sha256)}",
        "",
    ]


def _md_fixed_steps() -> list[str]:
    lines = [
        "## Baslinjens fasta steg",
        "",
        "Samma steg i samma ordning för varje fråga, utan att modellen väljer något av dem. "
        "Ett verktyg som svarar med fel noteras, och flödet går vidare.",
        "",
    ]
    lines += [f"{number}. {step}" for number, step in enumerate(FIXED_STEPS, start=1)]
    return [*lines, ""]


def _commit(info: RunInfo) -> str:
    """The commit measured, where it came from, and what code it is the commit of."""
    if info.commit is None:
        commit = (
            "okänd commit (git gick inte att fråga där mätningen kördes, och "
            f"`{COMMIT_VARIABLE}` gav ingen commit: inte satt, eller inte en sha, se loggen)"
        )
    elif info.commit_source == "environment":
        commit = (
            f"commit `{info.commit}` enligt `{COMMIT_VARIABLE}` (git gick inte att fråga, så om "
            "koden hade ändringar som inte var incheckade syns inte)"
        )
    elif info.uncommitted:
        commit = f"commit `{info.commit}`, med ändringar som inte var incheckade"
    else:
        commit = f"commit `{info.commit}`"
    if info.mcp == "stdio":
        return (
            f"{commit}; gäller agenten och mätningen, och avtal-mcp, som kördes som barnprocess "
            "ur samma installation"
        )
    return (
        f"{commit}; gäller agenten och mätningen, medan avtal-mcp på {_md(info.mcp)} kan vara "
        "en annan version eller den tillfälliga ersättaren"
    )


def _sha(sha256: str | None) -> str:
    return f"`{sha256[:12]}`" if sha256 else NOT_SAVED


def _md_method(report: AnswerReport) -> list[str]:
    lines = [
        "## Vad som mättes och hur",
        "",
        "- Varje fråga ställs i ett eget samtal, med en egen session mot avtal-mcp som i API:t. "
        "Frågar agenten användaren svarar mätningen med frågans förtydligande när "
        "testsamlingen har ett (en oklar fråga, där facit bygger på förtydligandet), och annars "
        f"”{ASK_USER_REPLY}”, eftersom guldfrågorna ska kunna besvaras som de är ställda.",
        "- **Bedömning:** domaren får frågan, facit, om avtalen besvarar frågan enligt facit och "
        "agentens svar (och förtydligandet, när agenten frågade och fick det), och dömer bara "
        "mot facit: *rätt* när svaret har kärnan i facit (det "
        "frågan efterfrågar) och inget i det motsäger facit, *delvis rätt* när det har en del "
        "av kärnan, annars *fel*. För en fråga som avtalen inte besvarar är ”framgår inte” "
        "rätt. Ett svar som inte blev klart inom gränsen för modellanrop är fel utan domare. "
        "Domarens skäl står per fråga, så att varje bedömning kan kontrolleras.",
        "- **Status** är den användaren ser: *Kontrollerat* när varje citat står ordagrant i "
        "sitt avsnitt, registeruppgifterna och ändringarna stämmer och granskaren fann stöd för "
        "svaret; *Med reservation* när något av det inte gick att kontrollera efter de nya "
        "försöken; *Inget svar* när agenten fann att avtalen inte besvarar frågan, eller inte "
        "kom fram till ett svar inom gränsen för modellanrop.",
        "- **Facits källor citerade:** en källa i facit räknas som citerad när svaret har ett "
        "citat som kontrollen godkände i samma fil och på samma plats i den (avsnittets "
        "position, eller dess nummer). Samma text i en fil som facit inte anger räknas inte, "
        "så talet är en undre gräns.",
        "- **Registeruppgifter:** avtalen i facits registerkällor jämförs med de avtal som "
        "svaret tar registeruppgifter ur, med samma nyckel som registret (-001 och -01 är samma "
        "avtal).",
        "- **Agentens väg:** varje verktygsanrop sparas i ordning med sina argument, också "
        "`ask_user` och varje utkast (`FinalAnswer`). Modellanropen är de som gränsen räknar "
        "(`ModelCallLimitMiddleware`), för hela frågan. Gränsen gäller per körning av grafen, "
        "och en fråga där agenten frågade användaren är två körningar, så den kan ha fler anrop "
        "än gränsen. Vägen sparas för varje fråga där körningen nådde agenten. En fråga där den "
        "aldrig gjorde det, som när sessionen mot avtal-mcp inte kom igång, har ingen väg: den "
        "står som inte sparad och räknas inte som noll. ”Frågor” i måtten för vägen, modellanropen "
        "och hänvisningarna är därför de frågor vars väg sparades.",
        "- **Mål ur en hänvisning:** ett `read_section` räknas hit när avsnittet det ber om "
        "(filen och platsen, eller filen och numret när platsen inte anges) står som mål under "
        "`references` i ett tidigare svar från `read_section` eller `resolve_reference` i samma "
        "fråga, det vill säga ett svar som kom före modellanropet som gjorde anropet. Ett mål "
        "som är en hel fil räknas inte. Målet kan också ha funnits i en sökträff, så måttet säger "
        "att agenten hade hänvisningen framför sig, inte varför den valde avsnittet. Det "
        "strängare måttet, ”ett mål som ingen tidigare sökning hade gett”, räknar bara de av dem "
        "där inget tidigare svar från `search_documents` i frågan hade avsnittet bland sina "
        "träffar eller träffarnas kopior (`copies`): agenten kan inte ha tagit dem ur en "
        "sökträff, bara ur hänvisningen eller ur en innehållsförteckning (`get_outline`). "
        "Anropen till `resolve_reference` räknas för sig, "
        "liksom ett `read_section` till ett avsnitt som ett tidigare `find_amendments` angav "
        "som ändring.",
        "- **Skälen till nya försök:** kontrollens återkoppling till agenten har en rad per fel, "
        "och varje rad räknas till regeln som skrev den, efter hur regeln formulerar sina fel: "
        "citat, registeruppgifter, senaste lydelsen eller granskaren. Granskaren körs bara när "
        "utkastet svarar på frågan (`answered` är sant) och de andra reglerna inte fann något "
        "fel; de andra kan underkänna samma utkast tillsammans. Ett utkast som flera regler "
        "underkände räknas under var och en, så talen per regel kan bli fler än de nya "
        "försöken. En rad som ingen regel känns igen på räknas till okänd regel.",
        "- **Tokens och kostnad** räknas för varje modellanrop, per modell, med priserna i "
        "arkitekturvalideringen (oktober 2026). Där saknas priset för cachad indata, så "
        "kostnaden anges från cachad indata utan kostnad till cachad indata till fullt pris; "
        "det verkliga priset ligger mellan. Domarens anrop räknas för sig.",
    ]
    if report.info.mode == "workflow":
        lines.append(
            "- **Baslinjen** har agentens modell, kontroll, granskare och gränser, men inga "
            "verktyg: modellen kan bara lämna svaret. Stegen före svaret är de fasta stegen "
            "ovan, och de står i vägen per fråga som agentens anrop gör. Modellanropen räknar "
            "också läsningen av frågan, som görs innan modellen skriver sitt första utkast, och "
            "den räknas mot gränsen."
        )
    if summarize_asks(report.results) is not None:
        lines.append(
            "- **Motfrågor:** för en fråga där testsamlingen säger att agenten ska fråga "
            "användaren (för att frågan passar flera avtal eller delområden med olika svar) "
            "räknas en motfråga som rätt bara när en domare (samma modell och nivå som för "
            "svaren) finner att den låter användaren välja mellan de väntade alternativen. För "
            "en fråga där svaret är detsamma i alla alternativ är det rätt att inte fråga, och "
            "en motfråga räknas som onödig. Svaret bedöms mot facit som förut, med "
            "förtydligandet när agenten frågade, och mot frågan som den ställdes när agenten "
            "inte frågade."
        )
    return [*lines, ""]


def _md_categories(results: Sequence[QuestionResult]) -> list[str]:
    lines = [
        "## Per kategori",
        "",
        _row(["Kategori", "Frågor", "Rätt", "Delvis", "Fel", "Kontrollerat", "Källor", "Tid"]),
        _row(["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"]),
    ]
    for category, group in by_category(results).items():
        s = summarize(group)
        sources = f"{s.sources_found} av {s.document_sources}" if s.document_sources else "–"
        lines.append(
            _row(
                [
                    _md(category),
                    str(s.questions),
                    str(s.verdicts["correct"]),
                    str(s.verdicts["partly_correct"]),
                    str(s.verdicts["incorrect"]),
                    str(s.statuses["verified"]),
                    sources,
                    _seconds(median(s.seconds)),
                ]
            )
        )
    lines += ["", "Tid är medianen per fråga.", ""]
    return lines


def _md_questions(results: Sequence[QuestionResult]) -> list[str]:
    lines = [
        "## Per fråga",
        "",
        _row(
            [
                "Fråga",
                "Kategori",
                "Bedömning",
                "Status",
                "Citat ✓",
                "Källor",
                "Register",
                "Nya försök",
                "Verktyg",
                "Modellanrop",
                "Tid",
                "Kostnad, högst",
            ]
        ),
        _row(["---"] * 4 + ["---:"] * 8),
    ]
    for r in results:
        status = STATUS_NAMES[r.status] if r.status else "Fel i körningen"
        lines.append(
            _row(
                [
                    _md(r.id),
                    _md(r.category),
                    _verdict_name(r),
                    status,
                    f"{r.verified_citations} av {r.citations}" if r.citations else "–",
                    _sources_cell(r),
                    _register_cell(r),
                    str(r.run.check_retries),
                    str(len(r.run.tools)),
                    str(r.run.model_calls) if r.run.model_calls is not None else "–",
                    _seconds(r.run.seconds),
                    _dollars(r.cost[1]) if r.cost is not None else "–",
                ]
            )
        )
    lines += [
        "",
        "Citat ✓: citat som står ordagrant i sitt avsnitt. Källor: facits källor som svaret "
        "citerar. Register: facits avtal bland svarets registerrader (+ avtal utöver facit). "
        "Verktyg: anrop till avtal-mcp. Modellanrop: agentens.",
        "",
    ]
    errors = [r for r in results if r.run.error]
    if errors:
        lines += ["Fel i körningen:", ""]
        lines += [f"- **{_md(r.id)}:** {_md(r.run.error or '')}" for r in errors]
        lines.append("")
    return lines


def _whose(info: RunInfo) -> str:
    """'Agentens', or 'Baslinjens' for a run of the baseline."""
    return "Baslinjens" if info.mode == "workflow" else "Agentens"


def _asks_line(asks: AskSummary) -> str:
    """'frågade när den borde i 3 av 4 frågor (2 med en motfråga som skiljer …); …'.

    Each half is left out when the gold has no question of its kind.
    """
    parts = []
    if asks.should_ask:
        part = (
            f"frågade när den borde i {asks.asked} av {asks.should_ask} frågor "
            f"({asks.separating} med en motfråga som skiljer alternativen åt"
        )
        if asks.unjudged:
            part += f", {asks.unjudged} ej bedömda"
        parts.append(part + ")")
    if asks.should_not_ask:
        parts.append(
            f"frågade i onödan i {asks.asked_unnecessarily} av {asks.should_not_ask} frågor"
        )
    return "; ".join(parts)


def _md_asks(report: AnswerReport) -> list[str]:
    """The section Motfrågor; nothing when no question says whether the agent should ask."""
    results = report.results
    asks = summarize_asks(results)
    if asks is None:
        return []
    who = "Baslinjen" if report.info.mode == "workflow" else "Agenten"
    lines = [
        "## Motfrågor",
        "",
        f"{who} {_asks_line(asks)}.",
        "",
        _row(["Fråga", "Ska fråga", "Frågade", "Skiljer", "Motfrågan", "Svaret"]),
        _row(["---", "---", "---", "---", "---", "---"]),
    ]
    asked_results = [r for r in results if r.should_ask is not None]
    for r in asked_results:
        separates = (
            ("ja" if r.ask_judgement.separates else "nej") if r.ask_judgement is not None else "–"
        )
        outcome = {True: "rätt", False: "fel", None: UNJUDGED_NAME.lower()}[r.asked_right]
        if r.unnecessary_ask:
            outcome = "onödig"
        lines.append(
            _row(
                [
                    _md(r.id),
                    "ja" if r.should_ask else "nej",
                    "ja" if r.asked else "nej",
                    separates,
                    outcome,
                    _verdict_name(r),
                ]
            )
        )
    lines += [
        "",
        "Skiljer: om motfrågan låter användaren välja mellan de väntade alternativen, enligt "
        "domaren; bara för en fråga där agenten skulle fråga och frågade. Motfrågan: rätt när "
        "agenten frågade med en motfråga som skiljer, eller inte frågade när den inte skulle. "
        "Svaret: domarens bedömning av svaret.",
        "",
    ]
    for r in asked_results:
        line = f"- **{_md(r.id)}** "
        line += "(ska fråga" if r.should_ask else "(ska svara utan att fråga"
        if r.expected_options:
            line += f"; väntade alternativ: {_listed(r.expected_options)[:-1]}"
        line += ")."
        if not r.asked:
            line += " Frågade inte."
        for number, question in enumerate(r.run.asked):
            offered = r.run.asked_options[number] if number < len(r.run.asked_options) else ()
            line += f" Frågade: ”{_md(question)}”"
            line += f" Alternativ: {_listed(offered)}" if offered else " (inga alternativ)."
        if r.ask_judgement is not None:
            verdict = "skiljer" if r.ask_judgement.separates else "skiljer inte"
            line += f" Domaren: {verdict}. {_md(r.ask_judgement.reason)}"
        lines.append(line)
    return [*lines, ""]


def _md_paths(report: AnswerReport) -> list[str]:
    paths = summarize_paths(report.results)
    lines = [f"## {_whose(report.info)} väg", ""]
    if paths.tools:
        lines += [
            "Anrop till avtal-mcp per verktyg, i alla frågor:",
            "",
            _row(["Verktyg", "Anrop", "Frågor"]),
            _row(["---", "---:", "---:"]),
        ]
        lines += [
            _row([_md(tool), str(n.calls), str(n.questions)]) for tool, n in paths.tools.items()
        ]
    else:
        lines.append("Agenten anropade inga verktyg i avtal-mcp.")
    lines.append("")
    if paths.rejections:
        lines += [
            "Nya försök efter regeln som underkände utkastet:",
            "",
            _row(["Regel", "Utkast", "Frågor", "Fel"]),
            _row(["---", "---:", "---:", "---:"]),
        ]
        lines += [
            _row([RULE_NAMES[rule], str(n.drafts), str(n.questions), str(n.problems)])
            for rule, n in paths.rejections.items()
        ]
        lines += [
            "",
            "Utkast: de som regeln underkände; ett utkast kan underkännas av flera regler. Fel: "
            "raderna i återkopplingen till agenten.",
            "",
        ]
    else:
        lines += ["Kontrollen skickade inte tillbaka något utkast.", ""]
    lines += [
        "### Vägen per fråga",
        "",
        "Varje anrop i ordning, med de argument som säger vad agenten letade efter (förkortade; "
        "alla står i JSON-rapporten, och där också om en tidigare sökning hade gett avsnittet, "
        "`found_by_search`). ↪ läste ett mål ur ett tidigare svars hänvisningar, Δ läste en "
        "ändring som ett tidigare `find_amendments` angav, ✗ verktyget svarade med fel. Ett "
        "utkast som kontrollen skickade tillbaka står som `FinalAnswer(underkänt: regel)`, ett "
        "vars form inte godtogs som `FinalAnswer(fel form)`. Det utkast som kontrollen släppte "
        "igenom till användaren står som `FinalAnswer`, eller som `FinalAnswer(med reservation)` "
        "när svaret fick status Med reservation: kontrollen underkände det också när de nya "
        "försöken var slut, eller något i det gick inte att kontrollera (reservationerna står "
        "under Bedömningar).",
        "",
    ]
    for r in report.results:
        lines += _md_path(r)
    return [*lines, ""]


def _model_calls(paths: PathSummary, info: RunInfo) -> str:
    """'median 7, högst 16, mot gränsen 16 per körning; 1 av 30 frågor hade 16 eller fler'."""
    calls, limit = paths.model_calls, info.model_call_limit
    if not calls:
        return NOT_SAVED
    at_limit = sum(1 for count in calls if count >= limit)
    text = (
        f"median {_number(median(calls))}, högst {max(calls)}, mot gränsen {limit} per körning; "
        f"{at_limit} av {len(calls)} frågor hade {limit} eller fler"
    )
    return text + _not_saved(paths)


def _reads(paths: PathSummary) -> str:
    """The reads of a reference's target, all and those no search had returned (see the method)."""
    if not paths.questions_saved:
        return NOT_SAVED
    text = (
        f"{paths.reads_from_references} av {paths.reads} anrop till `read_section` "
        f"({_percent(paths.reads_from_references, paths.reads)}) gick till ett mål ur ett "
        f"tidigare svars hänvisningar, i {paths.questions_from_references} av "
        f"{paths.questions_saved} frågor, och {paths.reads_from_references_only} av dem till ett "
        "mål som ingen tidigare sökning hade gett, i "
        f"{paths.questions_from_references_only} av {paths.questions_saved} frågor; "
        f"{paths.reads_from_amendments} anrop gick till en ändring som `find_amendments` angav"
    )
    return text + _not_saved(paths)


def _resolved(paths: PathSummary) -> str:
    """'3 anrop, i 2 av 30 frågor'."""
    if not paths.questions_saved:
        return NOT_SAVED
    calls = paths.tools.get(RESOLVE_REFERENCE, ToolCount(0, 0))
    text = f"{calls.calls} anrop, i {calls.questions} av {paths.questions_saved} frågor"
    return text + _not_saved(paths)


def _not_saved(paths: PathSummary) -> str:
    return f" ({NOT_SAVED} för {_questions(paths.not_saved)})" if paths.not_saved else ""


def _md_path(result: QuestionResult) -> list[str]:
    """The question's path on one line, and each problem of each draft sent back below it."""
    run = result.run
    if not run.path_saved:
        return [
            f"- **{_md(result.id)}:** {NOT_SAVED}" + (" (fel i körningen)" if run.error else "")
        ]
    about = [f"{run.model_calls} modellanrop"]
    if counts := rule_counts(run.rejections):
        about.append(
            "nya försök efter regel: "
            + ", ".join(f"{RULE_NAMES[rule]} {n}" for rule, n in counts.items())
        )
    lines = [f"- **{_md(result.id)}** ({'; '.join(about)}): {_path(run)}"]
    for number, rejection in enumerate(run.rejections, 1):
        lines += [
            f"  - Skäl till nytt försök {number}, {RULE_NAMES[problem.rule]}: "
            f"{_md(_short(problem.text, PROBLEM_CHARS))}"
            for problem in rejection.problems
        ]
    return lines


def _path(run: QuestionRun) -> str:
    if not run.steps:
        return "inga verktygsanrop"
    rejections = iter(run.rejections)
    reserved = run.answer is not None and run.answer.status == "with_reservation"
    return " → ".join(_step(step, rejections, reserved) for step in run.steps)


def _attempts(count: int) -> str:
    return "1 nytt försök" if count == 1 else f"{count} nya försök"


def _questions(count: int) -> str:
    return "1 fråga" if count == 1 else f"{count} frågor"


def _step(step: Step, rejections: Iterator[Rejection], reserved: bool) -> str:
    """One call as the path shows it: 'read_section(9.9.2)', with its marks.

    `reserved`: the answer has status with_reservation, so the draft it came from is marked.
    """
    if step.name == FINAL_ANSWER_TOOL:
        if step.draft == "sent_back":
            rejection = next(rejections, None)
            rules = ", ".join(RULE_NAMES[rule] for rule in rejection.rules) if rejection else ""
            return f"FinalAnswer(underkänt{': ' + rules if rules else ''})"
        if step.draft == "refused":
            return "FinalAnswer(fel form)"
        if step.draft == "submitted" and reserved:
            return "FinalAnswer(med reservation)"
        return "FinalAnswer"
    mark = {"reference": "↪", "amendment": "Δ"}.get(step.target_from or "", "")
    text = mark + _md(step.name)
    if (shown := _shown_args(step)) is not None:
        text += f"({', '.join(_md(arg) for arg in shown)})"
    return text + ("✗" if step.error else "")


# The arguments a call shows in the path, by tool, in order: what it looked for. "section" is
# the section's number, else its position, else the file; "file" is the start of the hash.
_SHOWN_ARGS: dict[str, tuple[str, ...]] = {
    "search_documents": ("query", "framework_area", "agreement_number", "document_type"),
    "search_register": (
        "supplier",
        "agreement_number",
        "org_number",
        "framework_area",
        "sub_area",
        "valid_on",
    ),
    "list_documents": ("framework_area", "agreement_number", "document_type"),
    "get_outline": ("file",),
    "read_section": ("section",),
    "resolve_reference": ("section", "reference"),
    "find_amendments": ("section",),
    "calculate_date": ("start", "direction", "amount", "unit"),
    "ask_user": ("question",),
}
_QUOTED_ARGS = {"query", "reference", "question"}  # free text, in quote marks


def _shown_args(step: Step) -> list[str] | None:
    """The call's shown arguments as text, each cut to ARG_CHARS; None for a tool not listed."""
    names = _SHOWN_ARGS.get(step.name)
    if names is None:
        return None
    shown = []
    for name in names:
        if name == "section":
            value = _section_arg(step.args)
        elif name == "file":
            value = _file_arg(step.args)
        else:
            value = step.args.get(name)
        if value is None or isinstance(value, bool) or str(value).strip() == "":
            continue
        text = _short(" ".join(str(value).split()), ARG_CHARS)
        shown.append(f'"{text}"' if name in _QUOTED_ARGS else text)
    return shown


def _section_arg(args: Mapping[str, Any]) -> str | None:
    number, position = args.get("section_number"), args.get("section_position")
    if isinstance(number, str) and number.strip():
        return number.strip()
    if position is not None:
        return f"plats {position}"
    return _file_arg(args)


def _file_arg(args: Mapping[str, Any]) -> str | None:
    sha256 = args.get("sha256")
    return f"{sha256[:12]}…" if isinstance(sha256, str) and sha256 else None


def _short(text: str, length: int) -> str:
    """`text` cut to at most `length` characters, an ellipsis marking the cut."""
    return text if len(text) <= length else f"{text[: length - 1].rstrip()}…"


def _md_usage(report: AnswerReport) -> list[str]:
    summary = summarize(report.results)
    judge = total_use(r.judge_usage for r in report.results)
    lines = [
        "## Tokens och kostnad",
        "",
        _row(
            [
                "Modell",
                "Roll",
                "Anrop",
                "Indata",
                "Varav cachad",
                "Utdata",
                "Varav resonemang",
                "Kostnad",
            ]
        ),
        _row(["---", "---", "---:", "---:", "---:", "---:", "---:", "---:"]),
    ]
    info = report.info
    for role, usage in (("agent och granskare", summary.usage), ("domare", judge)):
        for model, tokens in usage.items():
            name = role
            if role != "domare":  # one model can be both
                roles = ((info.agent_model, "agent"), (info.reviewer_model, "granskare"))
                name = " och ".join(label for m, label in roles if m == model) or role
            dollars = cost_range({model: tokens})
            lines.append(
                _row(
                    [
                        _md(model),
                        name,
                        _n(tokens.calls),
                        _n(tokens.input_tokens),
                        _n(tokens.cached_input_tokens),
                        _n(tokens.output_tokens),
                        _n(tokens.reasoning_tokens),
                        _dollars_range(*dollars) if dollars is not None else "–",
                    ]
                )
            )
    prices = "; ".join(
        f"{_md(model)} {_decimal(price.input, 2)} / {_decimal(price.output, 2)}"
        for model, price in PRICES.items()
    )
    lines += [
        "",
        f"Pris i dollar per miljon tokens, indata / utdata: {prices}. Kostnaden går från "
        "cachad indata utan kostnad till cachad indata till fullt pris. En modell utan pris får "
        "ingen kostnad.",
        "",
    ]
    return lines


def _judge_failed(result: QuestionResult) -> bool:
    """Whether the judge was asked about the answer but gave no verdict."""
    return result.run.answer is not None and result.judgement is None


def _md_judgements(report: AnswerReport) -> list[str]:
    lines = ["## Bedömningar", ""]
    for r in report.results:
        line = f"- **{_md(r.id)} {_verdict_name(r)}.**"
        if r.judgement is not None:
            line += f" {_md(r.judgement.reason)}"
            if r.judgement.missing:
                line += f" Saknas: {_listed(r.judgement.missing)}"
            if r.judgement.wrong:
                line += f" Motsäger facit: {_listed(r.judgement.wrong)}"
            if r.judged_by == "rule":
                line += " (Bedömd utan domare.)"
        elif r.run.error:
            line += " Körningen gav inget svar att bedöma."
        elif report.info.judge_model is not None and _judge_failed(r):
            line += " Domaren svarade inte; varför står i loggen."
        if r.run.answer is not None and r.run.answer.reservations:
            line += f" Reservationer: {_listed(r.run.answer.reservations)}"
        lines.append(line)
    return [*lines, ""]


def _md_answers(results: Sequence[QuestionResult]) -> list[str]:
    lines = ["## Svaren", ""]
    for r in results:
        lines += [
            f"### {_md(r.id)}: {_md(r.category)}",
            "",
            f"**Fråga:** {_md(r.question)}",
            "",
            f"**Facit:** {_md(r.gold_answer)}",
            "",
        ]
        answer = r.run.answer
        if answer is None:
            lines += ["**Svar:** inget (fel i körningen).", ""]
            continue
        lines += [f"**Svar ({STATUS_NAMES[answer.status]}):**", "", *_quoted(answer.text), ""]
        for citation in answer.citations:
            heading = " ".join(filter(None, (citation.section_number, citation.section_title)))
            mark = "✓" if citation.verified else "✗"
            lines.append(
                f"- [{citation.id}] {_md(citation.file_title or citation.sha256[:12])}, "
                f"{_md(heading)} {mark}"
            )
        if answer.citations:
            lines.append("")
    return lines


def _listed(items: Sequence[str]) -> str:
    """Items as one sentence: 'a; b.', without a second full stop after an item's own."""
    return _md("; ".join(item.strip().rstrip(".;") for item in items)) + "."


def _quoted(text: str) -> list[str]:
    """Text as a Markdown block quote, its lines kept and markup characters escaped."""
    return [f"> {_md(line)}" if line.strip() else ">" for line in text.strip().splitlines()]


def _sources_cell(result: QuestionResult) -> str:
    if not result.document_sources:
        return "–"
    return f"{sum(result.sources_found)} av {result.document_sources}"


def _register_cell(result: QuestionResult) -> str:
    if not result.register_required:
        return "–"
    cell = f"{result.register_found} av {result.register_required}"
    return cell + (f" (+{result.register_extra})" if result.register_extra else "")


def _verdict_name(result: QuestionResult) -> str:
    return VERDICT_NAMES[result.verdict] if result.verdict else UNJUDGED_NAME


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _md(text: str) -> str:
    """Text for a table cell or a line: on one line, with markup characters escaped."""
    return " ".join(text.split()).translate(_MARKUP)


def _decimal(value: float, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def _number(value: float) -> str:
    """A whole number as it is, else with one decimal: '7', '7,5'."""
    return str(int(value)) if value == int(value) else _decimal(value, 1)


def _percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole) if whole else 0}{_NBSP}%"


def _seconds(value: float) -> str:
    return f"{round(value)}{_NBSP}s"


def _dollars(value: float) -> str:
    return f"{_decimal(value, 2 if value >= 0.1 else 3)}{_NBSP}USD"


def _dollars_range(low: float, high: float) -> str:
    """'0,12–0,26 USD'; one amount when both are the same as written."""
    low_text, high_text = _dollars(low), _dollars(high)
    if low_text == high_text:
        return high_text
    return f"{low_text.removesuffix(f'{_NBSP}USD')}–{high_text}"


def _n(value: int) -> str:
    return f"{value:,}".replace(",", _NBSP)


def _moment(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M:%S} (UTC)"


def overall_lines(report: AnswerReport) -> list[str]:
    """The overall numbers as printed."""
    s = summarize(report.results)
    paths = summarize_paths(report.results)
    v = s.verdicts
    n = s.questions
    statuses = ", ".join(f"{status} {s.statuses[status]}" for status in (*STATUSES, "error"))
    mode = ", workflow" if report.info.mode == "workflow" else ""
    lines = [
        f"Answer evaluation, {report.info.agent_model} ({report.info.agent_effort}{mode}): "
        f"{n} questions in {report.seconds:.0f} s",
        f"judge: correct {v['correct']}, partly {v['partly_correct']}, incorrect "
        f"{v['incorrect']}, unjudged {v[_UNJUDGED]}"
        if report.info.judge_model is not None
        else "judge: none (--no-judge)",
        f"status: {statuses}",
        f"citations verified {s.verified_citations}/{s.citations}; gold sources cited "
        f"{s.sources_found}/{s.document_sources}; register agreements "
        f"{s.register_found}/{s.register_required} (+{s.register_extra})",
        f"seconds: median {median(s.seconds):.0f}, p90 {percentile(s.seconds, 0.9):.0f}; "
        f"retried {s.retried}; asked the user {s.asked}",
        f"model calls: {_printed_model_calls(paths, report.info.model_call_limit)}",
        f"references: {_printed_reads(paths)}",
        "retries by rule (a draft under each rule that sent it back): "
        + (", ".join(f"{rule} {n.drafts}" for rule, n in paths.rejections.items()) or "none"),
    ]
    if (asks := summarize_asks(report.results)) is not None:
        lines.append(
            f"asks: asked when it should {asks.asked}/{asks.should_ask} (separating "
            f"{asks.separating}, unjudged {asks.unjudged}); asked unnecessarily "
            f"{asks.asked_unnecessarily}/{asks.should_not_ask}"
        )
    if s.cost is not None:
        lines.append(f"cost: ${s.cost[0]:.2f}-{s.cost[1]:.2f} (agent and reviewer)")
    return lines


def _printed_not_saved(paths: PathSummary) -> str:
    if not paths.not_saved:
        return ""
    return f"; not saved for {paths.not_saved} question{'s' if paths.not_saved != 1 else ''}"


def _printed_model_calls(paths: PathSummary, limit: int) -> str:
    calls = paths.model_calls
    if not calls:
        return f"not saved (limit {limit} per run)"
    text = f"median {median(calls):g}, max {max(calls)} (limit {limit} per run)"
    return text + _printed_not_saved(paths)


def _printed_reads(paths: PathSummary) -> str:
    if not paths.questions_saved:
        return "not saved"
    resolved = paths.tools.get(RESOLVE_REFERENCE, ToolCount(0, 0)).calls
    text = (
        f"read_section to a referenced target {paths.reads_from_references}/{paths.reads}, "
        f"of them not returned by an earlier search {paths.reads_from_references_only}; "
        f"resolve_reference calls {resolved}"
    )
    return text + _printed_not_saved(paths)


def check_writable(out_dir: Path) -> None:
    """Stop with a CommandError unless reports can be written to `out_dir`.

    Run before the questions, so a folder the user may not write to (the
    Compose service runs as uid 1000) does not lose a run that has been paid for.
    """
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=out_dir, prefix=".write-check-"):
            pass
    except OSError as error:
        raise CommandError(
            f"Rapporterna gick inte att skriva till {out_dir}: {error.strerror}"
        ) from None


def write_reports(report: AnswerReport, out_dir: Path) -> tuple[Path, Path]:
    """Write the Markdown and JSON reports to `out_dir`; return both paths."""
    stem = report_stem(report)
    markdown, data = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(report), encoding="utf-8")
        data.write_text(report_json(report), encoding="utf-8")
    except OSError as error:
        raise CommandError(
            f"Rapporterna gick inte att skriva till {out_dir}: {error.strerror}"
        ) from None
    return markdown, data
