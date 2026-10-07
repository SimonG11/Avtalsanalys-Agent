"""The answer evaluation's reports: Markdown in Swedish, and JSON (M11).

What:
    `AnswerReport` holds a run: when, on which gold file, how (`RunInfo`)
    and every `QuestionResult`. `render_markdown` writes the Swedish report:
    the summary, the run, the method, a table per category and per
    question, the tokens and cost by model, the judge's reason for each
    verdict, and each answer next to the gold answer. `report_json` holds
    the same, with each answer as the web app gets it; `overall_lines` is
    what the command prints, and `write_reports` writes both files.

Why:
    The Markdown is what is shown and read: the numbers first, then what it
    takes to check them, the judge's reasons and the answers themselves. The
    JSON is for comparing runs. Unlike the search's reports, these hold the
    questions, the answers and their quotes, since a verdict cannot be
    checked without them; git ignores evals/reports/, so they stay on the
    machine that ran them.

How:
    Pure functions over the results and `summarize`. Numbers are written
    the Swedish way (a decimal comma, a no-break space before "%"), and text
    in a table is put on one line with markup characters escaped. The cost
    is a range: cached input at no cost, and at the full input price.
"""

import json
import re
import statistics
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from avtalsagent.agent.__main__ import STATUS_NAMES, CommandError
from evals.answer_run import ASK_USER_REPLY, PRICES, TokenUse, cost_range, total_use
from evals.answer_scores import (
    STATUSES,
    QuestionResult,
    Summary,
    by_category,
    median,
    percentile,
    summarize,
)

VERDICT_NAMES = {"correct": "Rätt", "partly_correct": "Delvis rätt", "incorrect": "Fel"}
UNJUDGED_NAME = "Ej bedömd"


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
    """`answers-<agent model>-<effort>[-<label>]`, safe as a file name."""
    parts = ["answers", report.info.agent_model, report.info.agent_effort]
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
        "questions": [_result_json(result) for result in report.results],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


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
        "refused_drafts": run.refused_drafts,
        "tools": list(run.tools),
        "tool_errors": run.tool_errors,
        "asked": list(run.asked),
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
    lines = [
        f"# Mätning av agentens svar: {_md(info.agent_model)}, resonemang {_md(info.agent_effort)}",
        "",
        "Guldfrågorna ställs till agenten med samma graf, kontroll och granskare som API:t "
        "kör. En domare (en språkmodell) jämför varje svar med facit, och svarets källor "
        "jämförs med facits.",
        "",
    ]
    lines += _md_summary(report)
    lines += _md_run(report)
    lines += _md_method(report)
    lines += _md_categories(report.results)
    lines += _md_questions(report.results)
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
            f"({report.info.model_call_limit} anrop)."
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
    lines.append(
        f"- **Nya försök:** kontrollen skickade tillbaka {s.retried} av {n} svar minst en gång "
        f"({s.retries} nya försök)."
    )
    if s.asked:
        lines.append(f"- **Följdfrågor:** agenten frågade användaren i {s.asked} av {n} frågor.")
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
    return [
        "## Körning",
        "",
        f"- **Tid:** {_moment(report.created_at)}, {_seconds(report.seconds)} totalt",
        f"- **Agent:** {_md(info.agent_model)}, resonemang {_md(info.agent_effort)}, högst "
        f"{info.model_call_limit} modellanrop per fråga, högst {info.validation_retries} nya "
        "försök",
        f"- **Granskare:** {_md(info.reviewer_model)}, resonemang {_md(info.reviewer_effort)}",
        f"- **Domare:** {judge}",
        f"- **avtal-mcp:** {_md(info.mcp)}",
        f"- **Guldfil:** {_md(report.gold_path)} (sha256 `{report.gold_sha256[:12]}`), "
        f"{len(report.results)} av {report.gold_questions} frågor",
        f"- **Samtidiga frågor:** {info.concurrency}; tidsgräns {_seconds(info.timeout)} per fråga",
        "",
    ]


def _md_method(report: AnswerReport) -> list[str]:
    return [
        "## Vad som mättes och hur",
        "",
        "- Varje fråga ställs i ett eget samtal, med en egen session mot avtal-mcp som i API:t. "
        "Frågar agenten användaren svarar mätningen "
        f"”{ASK_USER_REPLY}”, eftersom guldfrågorna ska kunna besvaras som de är ställda.",
        "- **Bedömning:** domaren får frågan, facit, om avtalen besvarar frågan enligt facit och "
        "agentens svar, och dömer bara mot facit: *rätt* när svaret har kärnan i facit (det "
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
        "- **Tokens och kostnad** räknas för varje modellanrop, per modell, med priserna i "
        "arkitekturvalideringen (oktober 2026). Där saknas priset för cachad indata, så "
        "kostnaden anges från cachad indata utan kostnad till cachad indata till fullt pris; "
        "det verkliga priset ligger mellan. Domarens anrop räknas för sig.",
        "",
    ]


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
                "Tid",
                "Kostnad, högst",
            ]
        ),
        _row(["---", "---", "---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"]),
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
                    _seconds(r.run.seconds),
                    _dollars(r.cost[1]) if r.cost is not None else "–",
                ]
            )
        )
    lines += [
        "",
        "Citat ✓: citat som står ordagrant i sitt avsnitt. Källor: facits källor som svaret "
        "citerar. Register: facits avtal bland svarets registerrader (+ avtal utöver facit). "
        "Verktyg: anrop till avtal-mcp.",
        "",
    ]
    errors = [r for r in results if r.run.error]
    if errors:
        lines += ["Fel i körningen:", ""]
        lines += [f"- **{_md(r.id)}:** {_md(r.run.error or '')}" for r in errors]
        lines.append("")
    return lines


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
    v = s.verdicts
    n = s.questions
    statuses = ", ".join(f"{status} {s.statuses[status]}" for status in (*STATUSES, "error"))
    lines = [
        f"Answer evaluation, {report.info.agent_model} ({report.info.agent_effort}): "
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
    ]
    if s.cost is not None:
        lines.append(f"cost: ${s.cost[0]:.2f}-{s.cost[1]:.2f} (agent and reviewer)")
    return lines


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
