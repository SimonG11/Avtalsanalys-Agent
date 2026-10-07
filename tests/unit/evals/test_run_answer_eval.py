"""Tests for evals.run_answer_eval: the run over the questions and the command line.

The agent's runs, avtal-mcp and the judge are replaced, so the order of the
results, the verdicts, the errors and the command line's output are tested
without a model, a key or a server. One question through the real graph is
tested in test_answer_run.py.
"""

import hashlib
import logging
import shutil
import subprocess
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx2
import openai
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr

import avtalsagent
from avtalsagent.agent.__main__ import CommandError
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.prompts import SYSTEM_PROMPT
from avtalsagent.agent.reviewer import REVIEWER_PROMPT
from avtalsagent.agent.schemas import Answer
from avtalsagent.config import Settings
from avtalsagent.observability.tracing import Tracing
from evals import run_answer_eval as runner
from evals.answer_report import AnswerReport, RunInfo
from evals.answer_run import QuestionRun, TokenUse
from evals.answer_scores import score
from evals.gold import GoldError, GoldFile, GoldQuestion, GoldScope, RegisterSource
from evals.judge import Judgement, ModelJudge
from evals.run_answer_eval import (
    Judge,
    ask_question,
    build_parser,
    check_mcp,
    code_commit,
    evaluate,
    judge_answer,
    run_info,
    run_settings,
    select_questions,
)

SECRET = "sk-test-not-a-real-key"
CORRECT = Judgement(verdict="correct", missing=[], wrong=[], reason="Samma som facit.")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def gold(id: str) -> GoldQuestion:
    return GoldQuestion(
        id=id,
        category="registerfråga",
        area="IT-drift",
        scope=GoldScope(),
        question=f"Fråga {id}?",
        answer="Svar.",
        answerable=True,
        sources=(RegisterSource(("23.3-5890-2023-002",), ("org_number",)),),
        why_hard="",
        difficulty=1,
    )


QUESTIONS = (gold("q01"), gold("q02"), gold("Q03"))
GOLD = GoldFile(Path("evals/datasets/gold_sv.jsonl"), "0" * 64, QUESTIONS)


def run(answer: Answer | None, error: str | None = None) -> QuestionRun:
    return QuestionRun(
        answer=answer,
        draft=None,
        tools=(),
        tool_errors=0,
        asked=(),
        check_retries=0,
        refused_drafts=0,
        seconds=1.0,
        usage={},
        error=error,
    )


ANSWERED = run(Answer(text="Svar.", status="verified", citations=[]))


class FakeJudge(Judge):
    """A judge that finds every answer correct and records what it read."""

    def __init__(self) -> None:
        self.read: list[str] = []

    async def judge(
        self, question: GoldQuestion, answer: Answer
    ) -> tuple[Judgement | None, Mapping[str, TokenUse]]:
        self.read.append(question.id)
        return CORRECT, {"judge-model": TokenUse(calls=1)}


def info(**changes: Any) -> RunInfo:
    values: dict[str, Any] = {
        "agent_model": "gpt-6.1-sol",
        "agent_effort": "low",
        "model_call_limit": 16,
        "validation_retries": 2,
        "reviewer_model": "gpt-6-astra",
        "reviewer_effort": "low",
        "judge_model": "gpt-6-astra",
        "judge_effort": "medium",
        "mcp": "stdio",
        "concurrency": 2,
        "timeout": 600.0,
        "label": None,
    }
    return RunInfo(**(values | changes))


# --- the questions and the settings -----------------------------------------------------------


def test_only_the_named_questions_are_asked_in_the_golds_order() -> None:
    assert select_questions(QUESTIONS, None) == list(QUESTIONS)
    assert [q.id for q in select_questions(QUESTIONS, ["q03", "Q01"])] == ["q01", "Q03"]
    with pytest.raises(GoldError, match="no question q09"):
        select_questions(QUESTIONS, ["q01", "q09"])


def test_the_measurement_keeps_its_checkpoints_in_memory() -> None:
    settings = Settings(_env_file=None, checkpointer="postgres", agent_reasoning_effort="low")

    assert run_settings(settings, None).checkpointer == "memory"
    assert run_settings(settings, None).agent_reasoning_effort == "low"
    assert run_settings(settings, "high").agent_reasoning_effort == "high"


def git(where: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(where), "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        + ["-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_commit_is_the_short_sha_and_whether_the_tree_has_changes(tmp_path: Path) -> None:
    git(tmp_path, "init", "--quiet")
    (tmp_path / "agent.py").write_text("PROMPT = 'ett'\n")
    git(tmp_path, "add", "agent.py")
    git(tmp_path, "commit", "--quiet", "-m", "first")
    sha = git(tmp_path, "rev-parse", "--short", "HEAD")

    assert code_commit(tmp_path) == (sha, False)
    (tmp_path / "agent.py").write_text("PROMPT = 'två'\n")
    assert code_commit(tmp_path) == (sha, True)
    git(tmp_path, "commit", "--quiet", "-am", "second")
    (tmp_path / "new.py").write_text("")  # neither tracked nor ignored
    assert code_commit(tmp_path)[1] is True


def test_without_git_or_a_repository_the_commit_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_git(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git")

    if shutil.which("git") is not None:  # a folder in no repository
        assert code_commit(tmp_path) == (None, False)
    monkeypatch.setattr(subprocess, "run", no_git)
    assert code_commit(tmp_path) == (None, False)


def test_the_run_info_names_the_commit_and_the_prompts_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    where: list[Path] = []

    def commit(path: Path) -> tuple[str | None, bool]:
        where.append(path)
        return "abc1234", True

    monkeypatch.setattr(runner, "code_commit", commit)
    args = build_parser().parse_args([])

    info = run_info(Settings(_env_file=None), args, None)

    assert (info.commit, info.uncommitted) == ("abc1234", True)
    assert where == [Path(avtalsagent.__file__).parent]  # the code that is measured
    assert info.system_prompt_sha256 == hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
    assert info.reviewer_prompt_sha256 == hashlib.sha256(REVIEWER_PROMPT.encode()).hexdigest()
    assert info.system_prompt_sha256 != info.reviewer_prompt_sha256


def test_the_options_have_their_defaults() -> None:
    args = build_parser().parse_args([])

    assert (args.concurrency, args.timeout, args.judge_effort) == (4, 600, "medium")
    assert (args.no_judge, args.judge_model, args.effort, args.only) == (False, None, None, None)
    assert args.gold == Path("evals/datasets/gold_sv.jsonl")
    assert args.out == Path("evals/reports")


# --- verdicts ---------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_verdict_is_by_rule_by_the_judge_or_none_without_a_judge() -> None:
    judge = FakeJudge()
    question = gold("q01")
    no_draft = run(Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[]))

    assert await judge_answer(judge, question, run(None, error="fel")) == (None, None, {})
    verdict, by, usage = await judge_answer(judge, question, no_draft)
    assert (verdict and verdict.verdict, by, usage) == ("incorrect", "rule", {})
    assert await judge_answer(None, question, ANSWERED) == (None, None, {})
    assert await judge_answer(None, question, no_draft) == (None, None, {})  # --no-judge
    assert await judge_answer(judge, question, ANSWERED) == (
        CORRECT,
        "judge",
        {"judge-model": TokenUse(calls=1)},
    )
    assert judge.read == ["q01"]


# --- avtal-mcp --------------------------------------------------------------------------------


@asynccontextmanager
async def unreachable(settings: Settings) -> AsyncIterator[McpTools]:
    raise ConnectionError(f"All connection attempts failed ({SECRET})")
    yield  # an async generator, as open_mcp_tools


@pytest.mark.anyio
async def test_a_server_that_is_not_there_stops_the_run_with_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "open_mcp_tools", unreachable)
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr(SECRET),
        mcp_transport="streamable_http",
        mcp_url="http://mcp:8001/mcp",
    )

    with pytest.raises(CommandError) as raised:
        await check_mcp(settings)

    message = str(raised.value)
    assert message.startswith("avtal-mcp svarar inte på http://mcp:8001/mcp")
    assert "ConnectionError" in message and SECRET not in message


@pytest.mark.anyio
async def test_a_question_whose_session_fails_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "open_mcp_tools", unreachable)
    settings = Settings(_env_file=None, openai_api_key=SecretStr(SECRET))

    result = await ask_question(settings, None, None, None, gold("q01"), 10)  # type: ignore[arg-type]

    assert result.answer is None
    assert result.error is not None and result.error.startswith("avtal-mcp: ConnectionError")
    assert SECRET not in result.error


def refused_key() -> openai.AuthenticationError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return openai.AuthenticationError(
        "Incorrect API key provided: sk-proj-****...WXYZ.",
        response=httpx2.Response(401, request=request),
        body=None,
    )


@pytest.mark.anyio
async def test_a_refused_key_is_not_recorded_as_the_questions_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def refusing(settings: Settings) -> AsyncIterator[McpTools]:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [refused_key()])
        yield

    monkeypatch.setattr(runner, "open_mcp_tools", refusing)
    settings = Settings(_env_file=None, openai_api_key=SecretStr(SECRET))

    with pytest.raises(ExceptionGroup):
        await ask_question(settings, None, None, None, gold("q01"), 10)  # type: ignore[arg-type]


# --- the run ----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_questions_run_at_once_and_the_results_keep_the_golds_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = 0
    most = 0
    traces: list[Any] = []

    async def ask(
        settings: Settings,
        model: Any,
        reviewer: Any,
        saver: Any,
        question: GoldQuestion,
        t: float,
        trace: Any = None,
    ) -> QuestionRun:
        nonlocal running, most
        traces.append(trace)
        running += 1
        most = max(most, running)
        await anyio.sleep({"q01": 0.05, "q02": 0.01, "Q03": 0.0}[question.id])
        running -= 1
        return run(None, error="tidsgräns") if question.id == "q02" else ANSWERED

    async def reachable(settings: Settings) -> None:
        return None

    monkeypatch.setattr(runner, "make_agent_model", lambda settings: object())
    monkeypatch.setattr(runner, "make_reviewer", lambda settings: object())
    monkeypatch.setattr(runner, "check_mcp", reachable)
    monkeypatch.setattr(runner, "ask_question", ask)
    judge = FakeJudge()

    report = await evaluate(
        Settings(_env_file=None),
        GOLD,
        QUESTIONS,
        concurrency=2,
        timeout=10,
        judge=judge,
        info=info(),
    )

    assert [r.id for r in report.results] == ["q01", "q02", "Q03"]
    assert [r.verdict for r in report.results] == ["correct", None, "correct"]
    assert report.results[1].run.error == "tidsgräns"
    assert [r.register_found for r in report.results] == [0, 0, 0]
    assert most == 2
    assert traces == [{}, {}, {}]  # tracing is off without Langfuse's keys
    assert sorted(judge.read) == ["Q03", "q01"]
    assert (report.gold_questions, report.gold_path) == (3, "evals/datasets/gold_sv.jsonl")


class RecordingTracing(Tracing):
    """Tracing that hands back what a run's trace would be named, instead of Langfuse's config."""

    def run_config(
        self,
        *,
        name: str,
        session_id: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> RunnableConfig:
        return {"metadata": {"name": name, "session": session_id, "tags": list(tags)}}


@pytest.mark.anyio
async def test_with_tracing_each_question_is_a_trace_named_by_its_id_in_the_runs_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traces: dict[str, Any] = {}

    async def ask(
        settings: Settings,
        model: Any,
        reviewer: Any,
        saver: Any,
        question: GoldQuestion,
        t: float,
        trace: Any = None,
    ) -> QuestionRun:
        traces[question.id] = trace["metadata"]
        return ANSWERED

    async def reachable(settings: Settings) -> None:
        return None

    monkeypatch.setattr(runner, "make_agent_model", lambda settings: object())
    monkeypatch.setattr(runner, "make_reviewer", lambda settings: object())
    monkeypatch.setattr(runner, "check_mcp", reachable)
    monkeypatch.setattr(runner, "ask_question", ask)

    await evaluate(
        Settings(_env_file=None),
        GOLD,
        QUESTIONS,
        concurrency=2,
        timeout=10,
        judge=None,
        info=info(label="stub"),
        tracing=RecordingTracing(),
    )

    assert {id: trace["name"] for id, trace in traces.items()} == {
        "q01": "q01",
        "q02": "q02",
        "Q03": "Q03",
    }
    (session,) = {trace["session"] for trace in traces.values()}
    assert session.startswith("eval-") and session.endswith("-stub")
    assert traces["q01"]["tags"] == ["eval", QUESTIONS[0].category]


# --- the command line -------------------------------------------------------------------------


def fixed_report(questions: tuple[GoldQuestion, ...], judge_model: str | None) -> AnswerReport:
    return AnswerReport(
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
        gold_path="evals/datasets/gold_sv.jsonl",
        gold_sha256="0" * 64,
        gold_questions=3,
        info=info(judge_model=judge_model, judge_effort="medium" if judge_model else None),
        seconds=12.0,
        results=tuple(score(q, ANSWERED, None, None) for q in questions),
    )


def test_the_command_writes_the_reports_and_prints_the_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gold_file = tmp_path / "gold.jsonl"
    gold_file.write_text(Path("evals/datasets/gold_sv.jsonl").read_text(encoding="utf-8"))
    seen: dict[str, Any] = {}

    async def fake_evaluate(settings: Settings, gold: GoldFile, questions: Any, **kw: Any) -> Any:
        seen.update(kw, settings=settings, questions=[q.id for q in questions])
        return fixed_report(tuple(questions), None)

    monkeypatch.setattr(runner, "evaluate", fake_evaluate)
    monkeypatch.setattr(runner, "get_settings", lambda: Settings(_env_file=None))

    runner.main(
        [
            "--gold",
            str(gold_file),
            "--only",
            "q21",
            "q01",
            "--no-judge",
            "--effort",
            "medium",
            "--concurrency",
            "2",
            "--label",
            "test",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    out = capsys.readouterr().out
    assert out.startswith("Answer evaluation, gpt-6.1-sol (low): 2 questions in 12 s\n")
    assert "Reports: " in out
    assert seen["questions"] == ["q01", "q21"]
    assert (seen["concurrency"], seen["timeout"], seen["judge"]) == (2, 600, None)
    assert seen["settings"].agent_reasoning_effort == "medium"
    assert seen["settings"].checkpointer == "memory"
    assert seen["info"].judge_model is None and seen["info"].label == "test"
    assert (tmp_path / "out" / "answers-gpt-6.1-sol-low.md").exists()


def test_an_unknown_question_stops_the_command(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(runner, "get_settings", lambda: Settings(_env_file=None))

    with caplog.at_level(logging.ERROR, logger="evals.answers"), pytest.raises(SystemExit) as end:
        runner.main(["--only", "q99", "--no-judge"])

    assert end.value.code == 1
    assert "no question q99" in caplog.text


def test_without_a_key_the_command_stops_before_any_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "get_settings", lambda: Settings(_env_file=None))

    async def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no question may be asked without a key")

    monkeypatch.setattr(runner, "evaluate", never)

    with pytest.raises(SystemExit) as end:
        runner.main(["--only", "q01"])

    assert end.value.code == 1
    assert "OPENAI_API_KEY saknas" in capsys.readouterr().err


def test_a_refused_key_ends_the_command_without_showing_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "get_settings", lambda: Settings(_env_file=None))

    async def refused(*args: Any, **kwargs: Any) -> Any:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [refused_key()])

    monkeypatch.setattr(runner, "evaluate", refused)

    with pytest.raises(SystemExit) as end:
        runner.main(["--only", "q01", "--no-judge", "--out", str(tmp_path)])

    assert end.value.code == 1
    err = capsys.readouterr().err
    assert "OpenAI tog inte emot nyckeln" in err and "WXYZ" not in err


def test_a_folder_that_cannot_be_written_stops_the_command_before_any_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "get_settings", lambda: Settings(_env_file=None))

    async def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no question may be asked when the reports cannot be written")

    monkeypatch.setattr(runner, "evaluate", never)
    blocked = tmp_path / "file"
    blocked.write_text("")

    with pytest.raises(SystemExit) as end:
        runner.main(["--only", "q01", "--no-judge", "--out", str(blocked / "reports")])

    assert end.value.code == 1
    assert "Rapporterna gick inte att skriva" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == [blocked]


def test_the_judges_model_needs_the_judge() -> None:
    with pytest.raises(SystemExit) as end:
        runner.main(["--no-judge", "--judge-model", "x"])

    assert end.value.code == 2


def test_the_judge_is_made_on_the_judge_model(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[tuple[str, str]] = []

    def make(settings: Settings, model: str, effort: str) -> Any:
        made.append((model, effort))
        return object()

    async def fake_evaluate(settings: Settings, gold: GoldFile, questions: Any, **kw: Any) -> Any:
        assert isinstance(kw["judge"], Judge)
        raise CommandError("stopp")

    monkeypatch.setattr(runner, "make_judge_model", make)
    monkeypatch.setattr(runner, "ModelJudge", lambda model: ModelJudge.__new__(ModelJudge))
    monkeypatch.setattr(runner, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        runner, "get_settings", lambda: Settings(_env_file=None, reviewer_model="the-reviewer")
    )

    with pytest.raises(SystemExit):
        runner.main(["--only", "q01"])
    with pytest.raises(SystemExit):
        runner.main(["--only", "q01", "--judge-model", "other", "--judge-effort", "high"])

    assert made == [("the-reviewer", "medium"), ("other", "high")]
