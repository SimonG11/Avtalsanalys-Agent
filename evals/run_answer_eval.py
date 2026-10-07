"""Measure the agent's answers on the gold questions: right, checked, sourced, time and cost (M11).

What:
    `python -m evals.run_answer_eval` asks the agent every gold question
    (evals/datasets/gold_sv.jsonl), with the graph, answer check and
    reviewer that the API runs, and scores each answer (`answer_scores`):
    - whether it is right: a judge model compares it with the gold answer
      (`judge`): correct, partly correct or incorrect, and why;
    - its status (verified, with a reservation, no answer) and how many of
      its quotes the citation check found word for word;
    - which of the gold's document sources it cites with a checked quote,
      and which of the gold's agreements its register facts cover;
    - how many new attempts the check asked for and why, which tools the
      agent called with what arguments, how many model calls it made,
      whether it asked the user, how long it took, and the tokens and cost
      of each model (`answer_run`, `answer_steps`).
    It writes a Markdown report in Swedish and a JSON report to
    evals/reports/ (`answers-<agent model>-<effort>[-<label>]`,
    `answer_report`) and prints the overall numbers.

        uv run python -m evals.run_answer_eval
        uv run python -m evals.run_answer_eval --only q01 q21 --effort medium
        docker compose --profile eval run --rm eval

Why:
    The search's measurement (M5) shows whether the right sections are
    found; the presentation needs what a user gets: whether the answer is
    right, whether it was checked, and what it costs. The check and the
    reviewer prove that an answer is supported by its sources, not that it
    is the answer the question needed, so a judge reads it next to the gold
    answer, and its reasons let a person check every verdict (ADR 0019).
    The agent runs as in the API, through avtal-mcp, so the same command
    measures the real database on a laptop (compose's `eval` service) or a
    stand-in server.

How:
    The models' clients are made first (without an OpenAI key the run stops
    there), then a session to avtal-mcp (MCP_TRANSPORT, MCP_URL) is opened
    and closed once, so a server that is not there stops the run before a
    question is asked. Each question then gets its own MCP session and
    graph, as each run of the API does (ADR 0014), and its own thread in a
    memory checkpointer; `--concurrency` questions run at once, and each is
    judged as soon as it is answered. A question whose session fails is
    recorded with its error, and the run goes on. Errors that stop the run
    end it with one line and exit code 1, as the agent's command line does
    (`describe_failure`); the OpenAI key and the database password are
    never printed. Before the questions, `run_info` notes what is measured:
    the commit of the repository the agent's code is imported from, and
    whether its working tree had changes (`code_commit`, by git; unknown
    without git), and the sha256 of SYSTEM_PROMPT (before the date is filled
    in) and of REVIEWER_PROMPT, so a report says which code and prompts it
    measured.
"""

import argparse
import asyncio
import hashlib
import logging
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

import avtalsagent
from avtalsagent.agent.__main__ import CommandError, describe_failure
from avtalsagent.agent.checkpointer import open_checkpointer
from avtalsagent.agent.graph import build_agent
from avtalsagent.agent.mcp_tools import open_mcp_tools
from avtalsagent.agent.model import make_agent_model
from avtalsagent.agent.prompts import SYSTEM_PROMPT
from avtalsagent.agent.reviewer import REVIEWER_PROMPT, make_reviewer
from avtalsagent.agent.schemas import Answer
from avtalsagent.config import Settings, get_settings
from avtalsagent.ingestion.__main__ import configure_logging, positive_int
from avtalsagent.observability.tracing import OFF as TRACING_OFF
from avtalsagent.observability.tracing import Tracing, open_tracing
from avtalsagent.validation.review import AnswerReviewer
from evals.answer_report import (
    AnswerReport,
    RunInfo,
    check_writable,
    overall_lines,
    write_reports,
)
from evals.answer_run import (
    ERROR_CHARS,
    QuestionRun,
    TokenUse,
    UsageCounter,
    error_text,
    run_question,
    stops_the_run,
)
from evals.answer_scores import JudgedBy, QuestionResult, rule_judgement, score
from evals.gold import GoldError, GoldFile, GoldQuestion, load_gold
from evals.judge import Judgement, ModelJudge, ReasoningEffort, make_judge_model

_log = logging.getLogger("evals.answers")

DEFAULT_GOLD = Path("evals/datasets/gold_sv.jsonl")
DEFAULT_OUT = Path("evals/reports")
DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT = 600  # seconds per question, new attempts and the review included
DEFAULT_JUDGE_EFFORT: ReasoningEffort = "medium"
EFFORTS: tuple[ReasoningEffort, ...] = ("low", "medium", "high", "xhigh")
GIT_TIMEOUT = 10  # seconds for each git command that finds the commit measured

# --- The run (models, avtal-mcp) --------------------------------------------------------------


class Judge:
    """The judge and the tokens of its calls."""

    def __init__(self, judge: ModelJudge) -> None:
        self._judge = judge

    async def judge(
        self, question: GoldQuestion, answer: Answer
    ) -> tuple[Judgement | None, Mapping[str, TokenUse]]:
        usage = UsageCounter()
        judgement = await self._judge.judge(
            question.question,
            question.answer,
            question.answerable,
            answer.text,
            config={"callbacks": [usage]},
        )
        return judgement, usage.by_model


async def ask_question(
    settings: Settings,
    model: BaseChatModel,
    reviewer: AnswerReviewer,
    checkpointer: BaseCheckpointSaver[str],
    question: GoldQuestion,
    timeout: float,
    trace: RunnableConfig | None = None,
) -> QuestionRun:
    """One question on its own MCP session and graph, as one run of the API."""
    started = time.monotonic()
    try:
        async with open_mcp_tools(settings) as mcp:
            graph = build_agent(model, mcp, reviewer, checkpointer, settings)
            return await run_question(
                graph,
                question.question,
                thread_id=f"eval-{question.id}-{uuid.uuid4()}",
                timeout=timeout,
                redact=settings.redact,
                trace=trace,
            )
    except Exception as error:  # the session to avtal-mcp failed; the run goes on
        if stops_the_run(error):
            raise
        message = settings.redact(f"avtal-mcp: {error_text(error)}")
        return QuestionRun(
            answer=None,
            draft=None,
            tools=(),
            tool_errors=0,
            asked=(),
            check_retries=0,
            refused_drafts=0,
            seconds=time.monotonic() - started,
            usage={},
            error=message[:ERROR_CHARS],
        )


async def judge_answer(
    judge: Judge | None, question: GoldQuestion, run: QuestionRun
) -> tuple[Judgement | None, JudgedBy | None, Mapping[str, TokenUse]]:
    """The verdict on the run's answer: by rule, by the judge, or none without a judge."""
    if run.answer is None or judge is None:
        return None, None, {}
    if (by_rule := rule_judgement(run)) is not None:
        return by_rule, "rule", {}
    judgement, usage = await judge.judge(question, run.answer)
    return judgement, "judge", usage


async def check_mcp(settings: Settings) -> None:
    """Open and close a session to avtal-mcp, so a server that is not there stops the run."""
    try:
        async with open_mcp_tools(settings):
            pass
    except Exception as error:
        where = (
            "som barnprocess (MCP_TRANSPORT=stdio)"
            if settings.mcp_transport == "stdio"
            else f"på {settings.mcp_url} (MCP_TRANSPORT=streamable_http)"
        )
        raise CommandError(
            settings.redact(f"avtal-mcp svarar inte {where}: {error_text(error)}")
        ) from error


async def evaluate(
    settings: Settings,
    gold: GoldFile,
    questions: Sequence[GoldQuestion],
    *,
    concurrency: int,
    timeout: float,
    judge: Judge | None,
    info: RunInfo,
    tracing: Tracing = TRACING_OFF,
) -> AnswerReport:
    """Ask the agent every question, `concurrency` at a time, and score the answers.

    With tracing on, each question is a trace named by its id, and the run their session.
    """
    model = make_agent_model(settings)  # without a key, before a server is started
    reviewer = make_reviewer(settings)
    await check_mcp(settings)
    created_at = datetime.now(UTC)
    session = f"eval-{created_at:%Y%m%dT%H%M%S}" + (f"-{info.label}" if info.label else "")
    started = time.monotonic()
    results: list[QuestionResult | None] = [None] * len(questions)
    limiter = anyio.CapacityLimiter(concurrency)
    done = 0

    async with open_checkpointer(settings) as checkpointer:

        async def one(index: int, question: GoldQuestion) -> None:
            nonlocal done
            async with limiter:
                trace = tracing.run_config(
                    name=question.id, session_id=session, tags=["eval", question.category]
                )
                run = await ask_question(
                    settings, model, reviewer, checkpointer, question, timeout, trace=trace
                )
                judgement, judged_by, judge_usage = await judge_answer(judge, question, run)
            result = score(question, run, judgement, judged_by, judge_usage)
            results[index] = result
            done += 1
            _log.info(
                "%s: %s, %s, %.0f s (%d of %d)",
                question.id,
                result.status or f"error ({run.error})",
                result.verdict or "unjudged",
                run.seconds,
                done,
                len(questions),
            )

        async with anyio.create_task_group() as group:
            for index, question in enumerate(questions):
                group.start_soon(one, index, question)

    return AnswerReport(
        created_at=created_at,
        gold_path=_shown_path(gold.path),
        gold_sha256=gold.sha256,
        gold_questions=len(gold.questions),
        info=info,
        seconds=time.monotonic() - started,
        results=tuple(result for result in results if result is not None),
    )


def _shown_path(path: Path) -> str:
    """The gold file's path for the reports: relative, so it names no home directory."""
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return path.name


# --- Command line -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_answer_eval",
        description="Measure the agent's answers on the gold questions.",
    )
    parser.add_argument(
        "--gold", type=Path, default=DEFAULT_GOLD, help=f"the gold file; default {DEFAULT_GOLD}"
    )
    parser.add_argument(
        "--only", nargs="+", metavar="ID", help="ask only these questions, e.g. --only q01 q21"
    )
    parser.add_argument(
        "--effort",
        choices=EFFORTS,
        help="the agent's reasoning effort; default AGENT_REASONING_EFFORT",
    )
    parser.add_argument(
        "--concurrency",
        type=positive_int,
        default=DEFAULT_CONCURRENCY,
        help=f"questions asked at once; default {DEFAULT_CONCURRENCY}",
    )
    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=DEFAULT_TIMEOUT,
        help=f"seconds per question before it is given up; default {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--no-judge", action="store_true", help="score without the judge (no verdicts)"
    )
    parser.add_argument("--judge-model", help="the judge's model; default REVIEWER_MODEL")
    parser.add_argument(
        "--judge-effort",
        choices=EFFORTS,
        default=DEFAULT_JUDGE_EFFORT,
        help=f"the judge's reasoning effort; default {DEFAULT_JUDGE_EFFORT}",
    )
    parser.add_argument("--label", help="added to the reports' names, e.g. --label stub")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"where the reports are written; default {DEFAULT_OUT}",
    )
    return parser


def select_questions(
    questions: Sequence[GoldQuestion], only: Sequence[str] | None
) -> list[GoldQuestion]:
    """The questions `--only` names, in the gold's order; all without it."""
    if not only:
        return list(questions)
    wanted = {item.casefold() for item in only}
    unknown = sorted(wanted - {question.id.casefold() for question in questions})
    if unknown:
        raise GoldError(f"the gold file has no question {', '.join(unknown)}")
    return [question for question in questions if question.id.casefold() in wanted]


def run_settings(settings: Settings, effort: ReasoningEffort | None) -> Settings:
    """The settings for the measurement: a memory checkpointer, and `--effort` if given."""
    changes: dict[str, Any] = {"checkpointer": "memory"}
    if effort is not None:
        changes["agent_reasoning_effort"] = effort
    return settings.model_copy(update=changes)


def code_commit(where: Path) -> tuple[str | None, bool]:
    """The short sha of the commit checked out at `where`, and whether the tree has changes.

    Changes are what `git status` lists: edits, and files git neither tracks
    nor ignores. (None, False) when git cannot tell: no git, or no repository
    (compose's `eval` container has neither).
    """
    try:
        head = _git(where, "rev-parse", "--short", "HEAD")
        status = _git(where, "status", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        return None, False
    return head.strip() or None, bool(status.strip())


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(where), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT,
    )
    return done.stdout


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_info(settings: Settings, args: argparse.Namespace, judge_model: str | None) -> RunInfo:
    commit, uncommitted = code_commit(Path(avtalsagent.__file__).parent)
    return RunInfo(
        agent_model=settings.agent_model,
        agent_effort=settings.agent_reasoning_effort,
        model_call_limit=settings.agent_model_call_limit,
        validation_retries=settings.validation_retries,
        reviewer_model=settings.reviewer_model,
        reviewer_effort=settings.reviewer_reasoning_effort,
        judge_model=judge_model,
        judge_effort=args.judge_effort if judge_model else None,
        mcp="stdio" if settings.mcp_transport == "stdio" else settings.mcp_url,
        concurrency=args.concurrency,
        timeout=float(args.timeout),
        label=args.label,
        commit=commit,
        uncommitted=uncommitted,
        system_prompt_sha256=sha256_of(SYSTEM_PROMPT),
        reviewer_prompt_sha256=sha256_of(REVIEWER_PROMPT),
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_judge and args.judge_model:
        parser.error("--judge-model needs the judge: leave out --no-judge")
    settings = run_settings(get_settings(), args.effort)
    configure_logging(settings.log_level)
    logging.getLogger("evals").setLevel(settings.log_level.upper())
    try:
        gold = load_gold(args.gold)
        questions = select_questions(gold.questions, args.only)
    except GoldError as error:
        _log.error("answer evaluation: %s", error)
        raise SystemExit(1) from None
    try:
        judge_model = None if args.no_judge else (args.judge_model or settings.reviewer_model)
        judge = (
            Judge(ModelJudge(make_judge_model(settings, judge_model, args.judge_effort)))
            if judge_model
            else None
        )
        check_writable(args.out)  # before the questions are paid for
        with open_tracing(settings) as tracing:
            report = asyncio.run(
                evaluate(
                    settings,
                    gold,
                    questions,
                    concurrency=args.concurrency,
                    timeout=args.timeout,
                    judge=judge,
                    info=run_info(settings, args, judge_model),
                    tracing=tracing,
                )
            )
        markdown, data = write_reports(report, args.out)
    except BaseException as error:  # Ctrl-C and the errors the user can act on; others go on
        failure = describe_failure(error, settings)
        if failure is None:
            raise
        code, message = failure
        print(message, file=sys.stderr, flush=True)
        raise SystemExit(code) from None
    for line in overall_lines(report):
        print(line)
    print(f"Reports: {markdown} and {data}")


if __name__ == "__main__":
    main()
