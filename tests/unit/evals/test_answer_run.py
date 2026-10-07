"""Tests for evals.answer_run: one question through the agent's graph, its tokens and cost.

The graph is the agent's own, run offline: a scripted model plays the
model's part and the test doubles of tests/unit/agent the readers and the
reviewer, so no test needs a network, an API key or a database.
"""

import uuid
from datetime import date
from typing import Any

import anyio
import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from evals.answer_run import (
    ASK_USER_REPLY,
    Price,
    TokenUse,
    UsageCounter,
    cost,
    cost_range,
    error_text,
    last_draft,
    run_question,
    token_use,
    total_use,
)
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)

SHA = "a1" * 32
SECTION = CitedSection(
    sha256=SHA,
    section_position=41,
    section_number="6.21.9",
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Mindre"],
    page_start=14,
    text="Kontraktet kan sägas upp av Kunden med en uppsägningstid om tre (3) månader.",
)
GOOD = {"id": 1, "sha256": SHA, "section_position": 41, "quote": "uppsägningstid om tre (3)"}
BAD = {"id": 1, "sha256": SHA, "section_position": 41, "quote": "uppsägningstid på tre månader"}
QUESTION = "Vilken uppsägningstid gäller?"
SECRET = "sk-test-not-a-real-key"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@tool
def search_documents(query: str) -> str:
    """Sök i dokumenten."""
    return f"Träff: sha256 {SHA}, section_position 41, avsnitt 6.21.9 Uppsägning."


class NamedModel(ScriptedModel):
    """A scripted model with a name, which LangChain reports as `ls_model_name`."""

    model: str = "the-agent-model"


class BrokenModel(NamedModel):
    """A model whose every call fails, with a secret in the error."""

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise ConnectionError(f"failed with {SECRET}")


class SlowModel(NamedModel):
    """A model that takes far longer than any test waits."""

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        await anyio.sleep(60)
        raise AssertionError("not reached")


def with_usage(message: AIMessage, tokens: int, cached: int = 0, reasoning: int = 0) -> AIMessage:
    message.usage_metadata = {
        "input_tokens": tokens,
        "output_tokens": 10,
        "total_tokens": tokens + 10,
        "input_token_details": {"cache_read": cached},
        "output_token_details": {"reasoning": reasoning},
    }
    return message


def build(model: ScriptedModel, retries: int = 1, limit: int = 16) -> AvtalAgent:
    settings = Settings(_env_file=None, validation_retries=retries, agent_model_call_limit=limit)
    mcp = McpTools(
        tools=[search_documents],
        reader=DictReader([SECTION]),
        register=ListRegister(),
        amendments=DictAmendments(),
    )
    return build_agent(
        model,
        mcp,
        ScriptedReviewer(),
        InMemorySaver(serde=serializer()),
        settings,
        today=lambda: date(2026, 10, 7),
    )


async def ask(graph: AvtalAgent, timeout: float = 30) -> Any:
    return await run_question(
        graph,
        QUESTION,
        thread_id=f"t-{uuid.uuid4()}",
        timeout=timeout,
        redact=lambda text: text.replace(SECRET, "***"),
    )


# --- one question -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_question_gives_the_answer_its_draft_its_tools_and_its_tokens() -> None:
    model = NamedModel(
        script=[
            with_usage(tool_call("search_documents", {"query": "uppsägning"}, "c1"), 100, 40, 5),
            with_usage(final_answer("Tre månader [1].", [GOOD], call_id="c2"), 200, 150),
        ]
    )

    run = await ask(build(model))

    assert run.error is None
    assert run.answer is not None and run.answer.status == "verified"
    assert run.draft is not None and run.draft.citations[0].section_position == 41
    assert run.tools == ("search_documents",)
    assert (run.tool_errors, run.check_retries, run.refused_drafts, run.asked) == (0, 0, 0, ())
    assert run.usage == {
        "the-agent-model": TokenUse(
            calls=2,
            input_tokens=300,
            cached_input_tokens=190,
            output_tokens=20,
            reasoning_tokens=5,
        )
    }
    assert run.seconds >= 0


@pytest.mark.anyio
async def test_the_agents_question_to_the_user_gets_the_fixed_reply() -> None:
    model = NamedModel(
        script=[
            tool_call("ask_user", {"question": "Vilket  avtal?", "options": ["A", "B"]}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )

    run = await ask(build(model))

    assert run.asked == ("Vilket avtal?",)
    assert run.tools == ()  # ask_user is the user, not a tool of avtal-mcp
    replies = [m for m in model.calls[1] if isinstance(m, ToolMessage) and m.name == "ask_user"]
    assert [reply.content for reply in replies] == [ASK_USER_REPLY]
    assert run.answer is not None and run.answer.status == "verified"


@pytest.mark.anyio
async def test_a_draft_the_check_sends_back_is_counted_and_the_retry_is_the_draft() -> None:
    model = NamedModel(
        script=[
            final_answer("Tre månader [1].", [BAD], call_id="c1"),
            final_answer("Tre (3) månader [1].", [GOOD], call_id="c2"),
        ]
    )

    run = await ask(build(model))

    assert run.check_retries == 1
    assert run.draft is not None and run.draft.text == "Tre (3) månader [1]."
    assert run.answer is not None and run.answer.status == "verified"


@pytest.mark.anyio
async def test_a_tools_error_is_counted() -> None:
    model = NamedModel(
        script=[
            tool_call("no_such_tool", {}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )

    run = await ask(build(model))

    assert run.tools == ("no_such_tool",)
    assert run.tool_errors == 1


@pytest.mark.anyio
async def test_a_failed_run_is_recorded_with_its_error_redacted() -> None:
    run = await ask(build(BrokenModel(script=[])))

    assert run.answer is None
    assert run.error is not None and "ConnectionError" in run.error
    assert SECRET not in run.error and "***" in run.error
    assert run.usage == {"the-agent-model": TokenUse(calls=1)}


@pytest.mark.anyio
async def test_a_run_past_the_timeout_is_given_up_and_recorded() -> None:
    run = await ask(build(SlowModel(script=[])), timeout=0.2)

    assert run.answer is None
    assert run.error == "tidsgränsen på 0.2 s nåddes"
    assert run.seconds < 5


@pytest.mark.anyio
async def test_a_run_at_the_model_call_limit_has_the_no_draft_answer() -> None:
    script = [tool_call("search_documents", {"query": "a"}, f"c{n}") for n in range(3)]
    graph = build(NamedModel(script=script), limit=2)

    run = await ask(graph)

    assert run.answer is not None and run.answer.text == NO_DRAFT_TEXT
    assert run.draft is None


def test_the_last_draft_is_the_last_final_answer_that_parses() -> None:
    first = final_answer("Ett.", [GOOD], call_id="c1")
    broken = tool_call("FinalAnswer", {"text": "Saknar answered."}, "c2")
    messages: list[BaseMessage] = [first, ToolMessage("x", tool_call_id="c1"), broken]

    draft = last_draft(messages)

    assert draft is not None and draft.text == "Ett."
    assert last_draft([]) is None


# --- tokens and cost --------------------------------------------------------------------------


def test_token_use_reads_the_reported_usage_of_a_call() -> None:
    message = with_usage(AIMessage("svar"), 1000, cached=600, reasoning=7)
    response = LLMResult(generations=[[ChatGeneration(message=message)]])

    assert token_use(response) == TokenUse(
        calls=1, input_tokens=1000, cached_input_tokens=600, output_tokens=10, reasoning_tokens=7
    )
    no_usage = LLMResult(generations=[[ChatGeneration(message=AIMessage("svar"))]])
    assert token_use(no_usage) == TokenUse(calls=1)


def test_the_counter_names_each_call_by_its_model() -> None:
    counter = UsageCounter()
    first, second, failed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    message = with_usage(AIMessage("svar"), 100)
    counter.on_chat_model_start({}, [], run_id=first, metadata={"ls_model_name": "sol"})
    counter.on_chat_model_start({}, [], run_id=second, invocation_params={"model": "astra"})
    counter.on_chat_model_start({}, [], run_id=failed, metadata={"ls_model_name": "sol"})
    counter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=first)
    counter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=second)
    counter.on_llm_error(TimeoutError(), run_id=failed)

    assert counter.by_model == {
        "sol": TokenUse(calls=2, input_tokens=100, output_tokens=10),
        "astra": TokenUse(calls=1, input_tokens=100, output_tokens=10),
    }


def test_the_cost_counts_cached_input_at_a_share_of_the_input_price() -> None:
    prices = {"sol": Price(input=2.0, output=10.0)}
    use = {
        "sol": TokenUse(
            calls=1, input_tokens=1_000_000, cached_input_tokens=600_000, output_tokens=100_000
        )
    }

    assert cost(use, cached=1.0, prices=prices) == pytest.approx(2.0 + 1.0)
    assert cost(use, cached=0.0, prices=prices) == pytest.approx(0.8 + 1.0)
    assert cost({"unknown": TokenUse(calls=1)}, cached=1.0, prices=prices) is None
    assert cost({}, cached=1.0, prices=prices) == 0.0


def test_the_cost_range_uses_the_architecture_prices() -> None:
    use = {"gpt-6.1-sol": TokenUse(calls=1, input_tokens=1_000_000, cached_input_tokens=500_000)}

    assert cost_range(use) == pytest.approx((1.0, 2.0))
    assert cost_range({"unknown": TokenUse(calls=1)}) is None


def test_total_use_adds_runs_by_model() -> None:
    one = {"sol": TokenUse(calls=1, input_tokens=5)}
    two = {"sol": TokenUse(calls=2, input_tokens=1), "astra": TokenUse(calls=1)}

    assert total_use([one, two]) == {
        "astra": TokenUse(calls=1),
        "sol": TokenUse(calls=3, input_tokens=6),
    }


def test_an_error_group_is_written_as_its_errors() -> None:
    group = ExceptionGroup("tasks", [ValueError("ett"), ExceptionGroup("inner", [KeyError("två")])])

    assert error_text(group) == "ValueError: ett; KeyError: 'två'"
