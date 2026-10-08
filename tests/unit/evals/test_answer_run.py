"""Tests for evals.answer_run: one question through the agent's graph, its tokens and cost.

The graph is the agent's own, run offline: a scripted model plays the
model's part and the test doubles of tests/unit/agent the readers and the
reviewer, so no test needs a network, an API key or a database.
"""

import json
import uuid
from datetime import date
from typing import Any

import anyio
import httpx2
import openai
import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import ANSWER_SUBMITTED, AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.schemas import Answer
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from avtalsagent.mcp_server.references import SectionReference, TargetRef
from avtalsagent.mcp_server.tools.read_section import Section
from avtalsagent.validation.review import ClaimReview, ReviewVerdict
from evals.answer_run import (
    ASK_USER_REPLY,
    MODEL_CALL_COUNT,
    Price,
    TokenUse,
    UsageCounter,
    cost,
    cost_range,
    error_text,
    last_draft,
    read_run,
    run_question,
    token_use,
    total_use,
)
from evals.answer_steps import Problem, Rejection, Step
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


# 6.21.9 as avtal-mcp's read_section gives it: it refers to 6.21.4 at position 37.
UPPSAGNING = Section(
    sha256=SHA,
    file_title="Allmänna villkor",
    document_type="general_terms",
    page_titles=["IT-drift Mindre"],
    section_position=41,
    section_number="6.21.9",
    section_title="Uppsägning",
    page_start=14,
    page_end=14,
    path=["6.21.9 Uppsägning"],
    text=SECTION.text + " Hävning regleras i punkt 6.21.4.",
    references=[
        SectionReference(
            raw="punkt 6.21.4",
            kind="section_number",
            status="resolved",
            targets=[
                TargetRef(
                    sha256=SHA,
                    file_title="Allmänna villkor",
                    document_type="general_terms",
                    page_titles=["IT-drift Mindre"],
                    section_position=37,
                    section_number="6.21.4",
                    section_title="Hävning",
                    page_start=13,
                    page_end=13,
                )
            ],
            held_back_targets=0,
        )
    ],
    held_back_targets=0,
)


@tool(response_format="content_and_artifact")
def read_section(
    sha256: str, section_number: str | None = None, section_position: int | None = None
) -> tuple[str, dict[str, Any]]:
    """Läs ett avsnitt."""
    # As langchain-mcp-adapters gives an avtal-mcp result: JSON text, the structure beside it.
    data = UPPSAGNING.model_dump(mode="json")
    return json.dumps(data, ensure_ascii=False), {"structured_content": data}


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


class RefusedKeyModel(NamedModel):
    """A model whose key OpenAI refuses; its message shows the key's last characters."""

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
        raise openai.AuthenticationError(
            "Incorrect API key provided: sk-proj-****...WXYZ.",
            response=httpx2.Response(401, request=request),
            body=None,
        )


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


def build(
    model: ScriptedModel,
    retries: int = 1,
    limit: int = 16,
    reviewer: ScriptedReviewer | None = None,
) -> AvtalAgent:
    settings = Settings(_env_file=None, validation_retries=retries, agent_model_call_limit=limit)
    mcp = McpTools(
        tools=[search_documents, read_section],
        reader=DictReader([SECTION]),
        register=ListRegister(),
        amendments=DictAmendments(),
    )
    return build_agent(
        model,
        mcp,
        reviewer or ScriptedReviewer(),
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
    assert run.steps == (
        Step("search_documents", {"query": "uppsägning"}),
        Step(
            "FinalAnswer",
            {"answered": True, "text": "Tre månader [1].", "citations": [GOOD]},
            draft="submitted",
        ),
    )
    assert (run.model_calls, run.rejections) == (2, ())
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
    assert run.steps[0] == Step("ask_user", {"question": "Vilket  avtal?", "options": ["A", "B"]})
    assert run.model_calls == 2  # one before the question to the user, one after the reply
    replies = [m for m in model.calls[1] if isinstance(m, ToolMessage) and m.name == "ask_user"]
    assert [reply.content for reply in replies] == [ASK_USER_REPLY]
    assert run.answer is not None and run.answer.status == "verified"


@pytest.mark.anyio
async def test_a_clarification_is_the_reply_and_each_questions_options_are_kept() -> None:
    model = NamedModel(
        script=[
            tool_call("ask_user", {"question": "Vilket delområde?", "options": ["1", "2"]}, "c1"),
            tool_call(
                "ask_user",
                {"question": "Och  kontraktet?", "options": ["Högst  ett år", "Längre"]},
                "c2",
            ),
            final_answer("Tre månader [1].", [GOOD], call_id="c3"),
        ]
    )

    run = await run_question(
        build(model),
        QUESTION,
        thread_id=f"t-{uuid.uuid4()}",
        timeout=30,
        redact=lambda text: text,
        clarification="Delområde 2.",
    )

    assert run.asked == ("Vilket delområde?", "Och kontraktet?")
    assert run.asked_options == (("1", "2"), ("Högst ett år", "Längre"))
    replies = [
        m.content for m in model.calls[2] if isinstance(m, ToolMessage) and m.name == "ask_user"
    ]
    assert replies == ["Delområde 2.", "Delområde 2."]
    assert ASK_USER_REPLY not in replies
    assert read_run({}, [], 1.0, {}, None).asked_options == ()


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
    # The reason is read from the feedback AnswerCheck gave the model.
    assert run.rejections == (
        Rejection(
            (
                Problem(
                    "citations",
                    "Källa [1]: citatet finns inte ordagrant i avsnitt 6.21.9 (Uppsägning). "
                    "Kopiera det ur texten från read_section, utan utelämningar och utan egna ord.",
                ),
            )
        ),
    )
    assert [step.draft for step in run.steps] == ["sent_back", "submitted"]
    assert run.model_calls == 2


@pytest.mark.anyio
async def test_a_draft_the_reviewer_sends_back_is_counted_under_the_review() -> None:
    doubted = ClaimReview(
        claim="Tre månader", citation_ids=[1], support="partly", reason="Gäller bara Kunden."
    )
    reviewer = ScriptedReviewer([ReviewVerdict(claims=[doubted], missing=["Vem som får säga upp"])])
    model = NamedModel(
        script=[
            final_answer("Tre månader [1].", [GOOD], call_id="c1"),
            final_answer("Kunden har tre (3) månader [1].", [GOOD], call_id="c2"),
        ]
    )

    run = await ask(build(model, reviewer=reviewer))

    (rejection,) = run.rejections
    assert rejection.rules == ("review",)
    assert [problem.text for problem in rejection.problems] == [
        "”Tre månader” [1]: stöds bara delvis. Gäller bara Kunden.",
        "Saknas: Vem som får säga upp.",
    ]


@pytest.mark.anyio
async def test_a_read_of_a_section_the_last_one_referred_to_is_marked() -> None:
    model = NamedModel(
        script=[
            tool_call("read_section", {"sha256": SHA, "section_number": "6.21.9"}, "c1"),
            tool_call("read_section", {"sha256": SHA, "section_position": 37}, "c2"),
            final_answer("Tre månader [1].", [GOOD], call_id="c3"),
        ]
    )

    run = await ask(build(model))

    assert [(step.name, step.args, step.target_from) for step in run.steps] == [
        ("read_section", {"sha256": SHA, "section_number": "6.21.9"}, None),
        ("read_section", {"sha256": SHA, "section_position": 37}, "reference"),
        ("FinalAnswer", {"answered": True, "text": "Tre månader [1].", "citations": [GOOD]}, None),
    ]
    assert run.model_calls == 3


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
    assert run.steps[0] == Step("no_such_tool", {}, error=True)


@pytest.mark.anyio
async def test_a_failed_run_is_recorded_with_its_error_redacted() -> None:
    run = await ask(build(BrokenModel(script=[])))

    assert run.answer is None
    assert run.error is not None and "ConnectionError" in run.error
    assert SECRET not in run.error and "***" in run.error
    assert run.usage == {"the-agent-model": TokenUse(calls=1)}
    # The state was read: no call finished, no tool was called.
    assert (run.model_calls, run.steps, run.rejections) == (0, (), ())


@pytest.mark.anyio
async def test_a_refused_key_stops_the_run_instead_of_being_recorded() -> None:
    # Every question would fail the same way, and the message shows part of the key.
    with pytest.raises(openai.AuthenticationError):
        await ask(build(RefusedKeyModel(script=[])))


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
    assert run.model_calls == 2  # the limit's: its note in place of a third call is no call
    assert len(run.steps) == 2


def test_a_malformed_argument_does_not_lose_the_answer() -> None:
    # "²".isdigit() holds, but int("²") raises: the run's answer must be kept all the same.
    answer = Answer(text="Tre månader.", status="verified", citations=[])
    messages: list[BaseMessage] = [
        HumanMessage(QUESTION),
        tool_call("read_section", {"sha256": SHA, "section_position": "²"}, "c1"),
        ToolMessage("Avsnittet finns inte.", tool_call_id="c1", name="read_section"),
        final_answer("Tre månader.", call_id="c2"),
        ToolMessage(ANSWER_SUBMITTED, tool_call_id="c2", name="FinalAnswer"),
    ]
    values = {"messages": messages, "answer": answer.model_dump(), MODEL_CALL_COUNT: 2}

    run = read_run(values, [], 1.0, {}, None)

    assert run.answer == answer
    assert run.steps[0] == Step("read_section", {"sha256": SHA, "section_position": "²"})
    assert (run.model_calls, run.path_saved) == (2, True)
    assert read_run({}, [], 1.0, {}, "fel").path_saved is False  # the state was empty


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
