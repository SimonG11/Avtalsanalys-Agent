"""Tests for avtalsagent.observability.tracing: tracing off without keys, and a question's trace.

The spans go to OpenTelemetry's in-memory exporter instead of Langfuse, so
no network or account is used. The run is the agent's own graph with a
scripted model, as in test_answer_run.py; the command line's conversation
and the measurement's question run it as they do in use.
"""

import io
import json
import uuid
from collections.abc import Sequence
from datetime import date

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from avtalsagent.agent import __main__ as cli
from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.reviewer import QUIET
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from avtalsagent.observability.tracing import OFF, open_tracing, traced, tracing_configured
from avtalsagent.validation.review import ReviewInput, ReviewVerdict
from evals.answer_run import run_question
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)

SECRET = "sk-lf-test-not-a-real-key"
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
OPTIONS = ["IT-drift Större", "IT-drift Mindre"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class NamedModel(ScriptedModel):
    model: str = "the-agent-model"


class ModelCallingReviewer(ScriptedReviewer):
    """Calls a runnable with a config of its own, as `ModelReviewer` calls its model."""

    async def review(self, request: ReviewInput) -> ReviewVerdict | None:
        model: RunnableLambda[str, str] = RunnableLambda(
            lambda messages: "ok", name="the-reviewer-model"
        )
        await model.ainvoke("granska", config={"metadata": dict(QUIET)})
        return await super().review(request)


@tool
def search_documents(query: str) -> str:
    """Search the agreements."""
    return "6.21.9 Uppsägning: uppsägningstid om tre (3) månader."


def settings(public: str | None = "", secret: str | None = SECRET) -> Settings:
    """Settings with Langfuse's keys; a new public key per call by default.

    Langfuse keeps one client per public key in the process, and a client
    that is shut down is not made again, so each test gets a key of its own.
    """
    return Settings(
        _env_file=None,
        langfuse_public_key=f"pk-lf-{uuid.uuid4()}" if public == "" else public,
        langfuse_secret_key=SecretStr(secret) if secret is not None else None,
        langfuse_base_url="http://127.0.0.1:9",  # never reached: the spans stay in memory
    )


def test_tracing_is_off_unless_both_keys_are_set() -> None:
    assert not tracing_configured(Settings(_env_file=None))
    assert not tracing_configured(settings(secret=None))
    assert not tracing_configured(settings(public=None))
    assert not tracing_configured(settings(secret=""))
    assert tracing_configured(settings())

    with open_tracing(settings(secret=None)) as tracing:
        assert tracing is OFF and not tracing.enabled
        assert tracing.run_config(name="fråga", session_id="t1", tags=["cli"]) == {}
        # The run's own config is used as it is: no callbacks, tags or metadata added.
        config: RunnableConfig = {"configurable": {"thread_id": "t1"}}
        assert traced(config, tracing.run_config(name="fråga")) is config


def test_the_secret_key_is_redacted_like_the_openai_key() -> None:
    assert settings().redact(f"401 with {SECRET}") == "401 with ***"
    assert SECRET not in repr(settings())


def test_a_run_config_carries_the_handler_and_the_traces_name_session_and_tags() -> None:
    exporter = InMemorySpanExporter()
    with open_tracing(settings(), span_exporter=exporter) as tracing:
        config = tracing.run_config(
            name="fråga", session_id="thread-1", tags=["api"], metadata={"run": "r1"}
        )
        no_session = tracing.run_config(name="fråga")

    (handler,) = config["callbacks"]  # type: ignore[misc]
    assert type(handler).__name__ == "LangchainCallbackHandler"
    assert config["metadata"] == {
        "run": "r1",
        "langfuse_trace_name": "fråga",
        "langfuse_session_id": "thread-1",
        "langfuse_tags": ["api"],
    }
    assert "langfuse_session_id" not in no_session["metadata"]


def agent(script: list[AIMessage]) -> AvtalAgent:
    """The agent's graph: a scripted model, the search tool, a reviewer that calls a model."""
    mcp = McpTools(
        tools=[search_documents],
        reader=DictReader([SECTION]),
        register=ListRegister(),
        amendments=DictAmendments(),
    )
    return build_agent(
        NamedModel(script=script),
        mcp,
        ModelCallingReviewer(),
        InMemorySaver(serde=serializer()),
        Settings(_env_file=None),
        today=lambda: date(2026, 10, 7),
    )


@pytest.mark.anyio
async def test_a_run_of_the_agent_is_one_trace_with_its_model_and_tool_calls() -> None:
    script = [
        tool_call("search_documents", {"query": "uppsägningstid"}, "c1"),
        final_answer("Tre månader [1].", [GOOD], call_id="c2"),
    ]
    script[0].usage_metadata = {"input_tokens": 1200, "output_tokens": 30, "total_tokens": 1230}
    graph = agent(script)
    exporter = InMemorySpanExporter()
    thread_id = f"t-{uuid.uuid4()}"

    with open_tracing(settings(), span_exporter=exporter) as tracing:
        config = traced(
            {"configurable": {"thread_id": thread_id}},
            tracing.run_config(name="fråga", session_id=thread_id, tags=["cli"]),
        )
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "Vilken uppsägningstid gäller?"}]}, config
        )
    # Leaving the block flushed the spans to the exporter.

    assert result["answer"].status == "verified"
    spans = exporter.get_finished_spans()
    assert len({span.context.trace_id for span in spans}) == 1
    (root,) = roots(spans)
    assert root.name == "avtalsagent"
    assert attribute(root, "session.id") == thread_id
    assert attribute(root, "langfuse.trace.tags") == ("cli",)
    generations = [s for s in spans if attribute(s, "langfuse.observation.type") == "generation"]
    assert len(generations) == 2
    assert {attribute(s, "langfuse.observation.model.name") for s in generations} == {
        "the-agent-model"
    }
    usage = [
        json.loads(str(attribute(s, "langfuse.observation.usage_details") or "{}"))
        for s in generations
    ]
    assert {"input": 1200, "output": 30, "total": 1230} in usage
    names = {span.name for span in spans}
    assert "search_documents" in names
    # The reviewer is called inside the answer check, with a config of its own: same trace.
    assert "the-reviewer-model" in names


@pytest.mark.anyio
async def test_each_question_is_a_trace_and_the_reply_to_the_agents_question_stays_in_it() -> None:
    graph = agent(
        [
            tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": OPTIONS}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
            final_answer("Också tre månader [1].", [GOOD], call_id="c3"),
        ]
    )
    typed = iter(["Vilken uppsägningstid gäller?", "2", "Gäller det Större också?"])
    terminal = cli.Terminal(
        out=io.StringIO(), log=io.StringIO(), read_line=lambda prompt: next(typed, None)
    )
    exporter = InMemorySpanExporter()

    with open_tracing(settings(), span_exporter=exporter) as tracing:
        await cli.converse(graph, None, terminal, as_json=False, tracing=tracing)

    spans = exporter.get_finished_spans()
    first, second = traces(spans)
    # The question stopped at ask_user and was resumed: two runs of the graph, one trace.
    assert [root.name for root in roots(first)] == ["avtalsagent", "avtalsagent"]
    assert [root.name for root in roots(second)] == ["avtalsagent"]
    sessions = {attribute(root, "session.id") for root in roots(spans)}
    assert len(sessions) == 1 and str(sessions.pop()).startswith("cli-")
    assert {attribute(root, "langfuse.trace.tags") for root in roots(spans)} == {("cli",)}


@pytest.mark.anyio
async def test_in_the_measurement_the_reply_to_the_agents_question_stays_in_its_trace() -> None:
    graph = agent(
        [
            tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": OPTIONS}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )
    exporter = InMemorySpanExporter()

    with open_tracing(settings(), span_exporter=exporter) as tracing:
        run = await run_question(
            graph,
            "Vilken uppsägningstid gäller?",
            thread_id="q14-thread",
            timeout=60,
            redact=str,
            trace=tracing.run_config(name="q14", session_id="eval-1", tags=["eval", "villkor"]),
        )

    assert run.asked == ("Vilket avtal menar du?",) and run.error is None
    (trace,) = traces(exporter.get_finished_spans())
    assert [attribute(root, "langfuse.trace.name") for root in roots(trace)] == ["q14", "q14"]


def test_the_openai_key_is_not_in_the_trace_of_a_model_call() -> None:
    key = "sk-proj-not-a-real-key-0123456789"
    model = ChatOpenAI(
        model="gpt-6.1-sol",
        api_key=SecretStr(key),
        base_url="http://127.0.0.1:9/v1",  # nothing listens: the call fails at once
        max_retries=0,
        use_responses_api=True,
    )
    exporter = InMemorySpanExporter()

    with (
        open_tracing(settings(), span_exporter=exporter) as tracing,
        pytest.raises(openai.APIConnectionError),
    ):
        model.invoke("Vilken uppsägningstid gäller?", tracing.run_config(name="fråga"))

    spans = exporter.get_finished_spans()
    assert [attribute(span, "langfuse.observation.type") for span in spans] == ["generation"]
    traced = repr([dict(span.attributes or {}) for span in spans])
    # The question is there (as JSON, so "ä" is escaped); the key is not.
    assert "Vilken upps" in traced
    assert key not in traced and key[8:] not in traced


def test_a_key_openai_shows_masked_in_its_401_is_masked_before_it_is_sent() -> None:
    key = "sk-proj-abcd0123456789wxyz"

    def unauthorized(request: httpx.Request) -> httpx.Response:
        message = (
            "Incorrect API key provided: sk-proj-abcd**************wxyz. You can find your API "
            "key at https://platform.openai.com/account/api-keys."
        )
        error = {"message": message, "type": "invalid_request_error", "code": "invalid_api_key"}
        return httpx.Response(401, json={"error": error})

    model = ChatOpenAI(
        model="gpt-6.1-sol",
        api_key=SecretStr(key),
        base_url="http://openai.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(unauthorized)),
        max_retries=0,
        use_responses_api=True,
    )
    exporter = InMemorySpanExporter()

    with (
        open_tracing(settings(), span_exporter=exporter) as tracing,
        pytest.raises(openai.AuthenticationError),
    ):
        model.invoke("Vilken uppsägningstid gäller?", tracing.run_config(name="fråga"))

    (span,) = exporter.get_finished_spans()
    status = str(attribute(span, "langfuse.observation.status_message"))
    assert "Incorrect API key provided: ***. You can find" in status
    traced_text = repr(dict(span.attributes or {}))
    assert "sk-proj-abcd" not in traced_text and "*wxyz" not in traced_text


def test_the_secrets_are_masked_in_every_text_that_is_sent() -> None:
    exporter = InMemorySpanExporter()
    keys = settings().model_copy(update={"openai_api_key": SecretStr("sk-proj-the-whole-key")})

    with open_tracing(keys, span_exporter=exporter) as tracing:
        assert tracing.client is not None
        tracing.client.start_observation(
            name="verktyg",
            input={"fel": f"401 med {SECRET} och sk-proj-the-whole-key"},
            metadata={"anrop": 3},
        ).end()

    (span,) = exporter.get_finished_spans()
    text = repr(dict(span.attributes or {}))
    assert SECRET not in text and "sk-proj-the-whole-key" not in text
    assert "401 med *** och ***" in text
    assert attribute(span, "langfuse.observation.metadata.anrop") == 3  # other values as they were


def test_leaving_the_block_with_an_error_still_sends_what_is_left() -> None:
    exporter = InMemorySpanExporter()
    with pytest.raises(RuntimeError), open_tracing(settings(), span_exporter=exporter) as tracing:
        assert tracing.client is not None
        tracing.client.start_observation(name="före felet").end()
        assert exporter.get_finished_spans() == ()  # still in the batch, not sent
        raise RuntimeError("the run failed")

    assert [span.name for span in exporter.get_finished_spans()] == ["före felet"]


def attribute(span: ReadableSpan, name: str) -> object:
    return (span.attributes or {}).get(name)


def roots(spans: Sequence[ReadableSpan]) -> list[ReadableSpan]:
    """The roots of the graph's runs, in the order they ended; they carry the trace's name."""
    return [span for span in spans if attribute(span, "langfuse.trace.name")]


def traces(spans: Sequence[ReadableSpan]) -> list[list[ReadableSpan]]:
    """The spans grouped by trace, in the order each trace's first span ended."""
    grouped: dict[int, list[ReadableSpan]] = {}
    for span in spans:
        grouped.setdefault(span.context.trace_id, []).append(span)
    return list(grouped.values())
