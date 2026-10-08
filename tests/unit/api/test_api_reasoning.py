"""Tests that the agent's reasoning summaries reach the web app, and nothing else with them.

The agent's model is the one `make_agent_model` builds, with its HTTP client
on a fake OpenAI: an httpx2 transport (the OpenAI client's HTTP library) that
answers each request to the Responses API with a prepared stream of
server-sent events, as OpenAI streams a reasoning item with its summary and
then a function call. So the test goes
through langchain-openai's parsing of the stream, the graph and the AG-UI
adapter as in the API, and reads the requests the model would have sent. The
app is the one of test_api_agui.py: avtal-mcp, the reviewer and the
checkpointer are fakes. No network, key or database is used.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx2
import openai
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from avtalsagent.agent.model import make_agent_model
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import PURPOSE_TEXT
from tests.unit.api.test_api_agui import (
    GOOD,
    OPTIONS,
    QUESTION,
    BreakingSessions,
    Sessions,
    app_on,
    client_of,
    of_type,
    post,
    run_input,
)

DUMMY_KEY = "sk-test-not-a-real-key"
THINKING = ["**Söker i avtalen**\n\nJag söker efter ", "uppsägningstiden i Allmänna villkor."]
CHECKING = "**Kontrollerar källan**\n\nAvsnittet anger tre månader."
SEARCH = {"syfte": PURPOSE_TEXT, "query": "uppsägningstid"}
ANSWER = {"answered": True, "text": "Uppsägningstiden är tre månader [1].", "citations": [GOOD]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Reply:
    """One response of the fake OpenAI: a reasoning item with its summary, then one call."""

    summary: list[list[str]]  # the summary's parts, each as the deltas it streams in
    call: str
    arguments: dict[str, Any]
    argument_deltas: int = 1  # how many deltas the call's arguments stream in


@dataclass
class FakeOpenAI:
    """Answers each request to the Responses API with the next reply, as a stream."""

    replies: list[Reply]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/responses")
        self.requests.append(json.loads(request.content))
        n = len(self.requests)
        events = list(stream_events(self.replies.pop(0), f"resp_{n}", f"rs_{n}", f"call_{n}"))
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})


def stream_events(
    reply: Reply, response_id: str, reasoning_id: str, call_id: str
) -> Iterator[dict[str, Any]]:
    """The events of one response, in the order and shape OpenAI streams them."""
    parts = [{"type": "summary_text", "text": "".join(deltas)} for deltas in reply.summary]
    reasoning = {"type": "reasoning", "id": reasoning_id, "summary": parts}
    arguments = json.dumps(reply.arguments, ensure_ascii=False)
    function_call = {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": reply.call,
        "arguments": arguments,
        "status": "completed",
    }
    yield {"type": "response.created", "response": response(response_id, "in_progress", [])}
    yield {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": reasoning | {"summary": []},
    }
    for index, deltas in enumerate(reply.summary):
        place = {"item_id": reasoning_id, "output_index": 0, "summary_index": index}
        empty = {"type": "summary_text", "text": ""}
        yield {"type": "response.reasoning_summary_part.added", **place, "part": empty}
        for delta in deltas:
            yield {"type": "response.reasoning_summary_text.delta", **place, "delta": delta}
        yield {"type": "response.reasoning_summary_text.done", **place, "text": "".join(deltas)}
        yield {"type": "response.reasoning_summary_part.done", **place, "part": parts[index]}
    yield {"type": "response.output_item.done", "output_index": 0, "item": reasoning}
    added = function_call | {"arguments": "", "status": "in_progress"}
    yield {"type": "response.output_item.added", "output_index": 1, "item": added}
    place = {"item_id": function_call["id"], "output_index": 1}
    size = -(-len(arguments) // reply.argument_deltas)
    for start in range(0, len(arguments), size):
        delta = arguments[start : start + size]
        yield {"type": "response.function_call_arguments.delta", **place, "delta": delta}
    yield {"type": "response.function_call_arguments.done", **place, "arguments": arguments}
    yield {"type": "response.output_item.done", "output_index": 1, "item": function_call}
    done = response(response_id, "completed", [reasoning, function_call])
    yield {"type": "response.completed", "response": done}


def response(response_id: str, status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": 1_791_000_000,
        "model": "the-agent-model",
        "status": status,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 60},
            "total_tokens": 1100,
        },
    }


def app_on_fake_openai(
    replies: list[Reply], sessions: Sessions | BreakingSessions | None = None
) -> tuple[FastAPI, FakeOpenAI]:
    """The app with the agent's model as `make_agent_model` builds it, on the fake OpenAI."""
    fake = FakeOpenAI(replies)
    settings = Settings(
        _env_file=None, openai_api_key=SecretStr(DUMMY_KEY), agent_model="the-agent-model"
    )
    model = make_agent_model(settings)
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handle))
    model.root_async_client = openai.AsyncOpenAI(api_key=DUMMY_KEY, http_client=http)
    return app_on(model, sessions=sessions), fake


def reasoning_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"].startswith("REASONING")]


def summary_text(events: list[dict[str, Any]], message_id: str) -> str:
    """The summary text the stream gave the reasoning message `message_id`."""
    return "".join(
        event["delta"]
        for event in of_type(events, "REASONING_MESSAGE_CONTENT")
        if event["messageId"] == message_id
    )


@pytest.mark.anyio
async def test_each_calls_summary_streams_as_reasoning_before_its_tool_calls() -> None:
    app, fake = app_on_fake_openai(
        [
            Reply([THINKING], "search_documents", SEARCH),
            Reply([[CHECKING[:20], CHECKING[20:]], ["Svaret är klart."]], "FinalAnswer", ANSWER),
        ]
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "RUN_ERROR") == []
    assert events[-1]["type"] == "RUN_FINISHED"
    # Thought, step, thought, step: each call's summary ends before its call starts.
    flow = [
        (event["type"], event.get("toolCallName"))
        for event in events
        if event["type"].startswith("REASONING") or event["type"] == "TOOL_CALL_START"
    ]
    one_message = [
        ("REASONING_START", None),
        ("REASONING_MESSAGE_START", None),
        *[("REASONING_MESSAGE_CONTENT", None)] * 2,
        ("REASONING_MESSAGE_END", None),
        ("REASONING_END", None),
    ]
    second_part = [
        ("REASONING_START", None),
        ("REASONING_MESSAGE_START", None),
        ("REASONING_MESSAGE_CONTENT", None),
        ("REASONING_MESSAGE_END", None),
        ("REASONING_END", None),
    ]
    assert flow == [
        *one_message,
        ("TOOL_CALL_START", "search_documents"),
        *one_message,
        *second_part,  # a summary in two parts is two reasoning messages
        ("TOOL_CALL_START", "FinalAnswer"),
    ]
    starts = [event["messageId"] for event in of_type(events, "REASONING_START")]
    assert starts[:2] == ["rs_1", "rs_2"]  # OpenAI's id of the reasoning item
    assert len(set(starts)) == 3
    assert summary_text(events, "rs_1") == "".join(THINKING)
    assert summary_text(events, "rs_2") == CHECKING
    assert summary_text(events, starts[2]) == "Svaret är klart."
    assert {e["role"] for e in of_type(events, "REASONING_MESSAGE_START")} == {"reasoning"}
    # Each reasoning message ends where it started.
    for kind in ("REASONING_MESSAGE_END", "REASONING_END"):
        assert [event["messageId"] for event in of_type(events, kind)] == starts
    # The answer is still never chat text, and a snapshot is still the answer alone.
    assert [event for event in events if event["type"].startswith("TEXT_MESSAGE")] == []
    snapshots = [event["snapshot"] for event in of_type(events, "STATE_SNAPSHOT")]
    assert all(set(snapshot) == {"answer"} for snapshot in snapshots)
    assert snapshots[-1]["answer"]["status"] == "verified"
    assert of_type(events, "REASONING_ENCRYPTED_VALUE") == []
    # The requests asked for the summary, and the second sent the first's reasoning back.
    assert [request["reasoning"] for request in fake.requests] == [
        {"effort": "low", "summary": "auto"}
    ] * 2
    assert items(fake.requests[1]) == [
        ("message", "system"),
        ("message", "user"),
        ("reasoning", "rs_1"),
        ("function_call", "call_1"),
        ("function_call_output", "call_1"),
    ]


@pytest.mark.anyio
async def test_the_messages_snapshot_holds_each_reasoning_item_before_its_message() -> None:
    app, _ = app_on_fake_openai(
        [
            Reply([THINKING], "search_documents", SEARCH),
            Reply([[CHECKING], ["Svaret är klart."]], "FinalAnswer", ANSWER),
        ]
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    messages = of_type(events, "MESSAGES_SNAPSHOT")[-1]["messages"]
    shown = [(m["role"], m.get("content") or "") for m in messages if m["role"] != "tool"]
    # A reasoning item is one message, its summary's parts joined by a line break, with
    # OpenAI's id as in the stream; the assistant's message after it carries the tool calls.
    assert shown == [
        ("user", QUESTION[0]["content"]),
        ("reasoning", "".join(THINKING)),
        ("assistant", ""),
        ("reasoning", f"{CHECKING}\nSvaret är klart."),
        ("assistant", ""),
    ]
    reasoning = [m for m in messages if m["role"] == "reasoning"]
    assert [m["id"] for m in reasoning] == ["rs_1", "rs_2"]
    assert all(m.get("encryptedValue") is None for m in reasoning)


@pytest.mark.anyio
async def test_after_ask_user_and_in_a_follow_up_the_reasoning_goes_back_once_in_order() -> None:
    question = {"question": "Gäller det er uppsägning eller leverantörens?", "options": OPTIONS}
    app, fake = app_on_fake_openai(
        [
            Reply([THINKING], "ask_user", question),
            Reply([[CHECKING]], "FinalAnswer", ANSWER),
            Reply([["**Samma villkor**\n\nMindre har samma avsnitt."]], "FinalAnswer", ANSWER),
        ]
    )

    async with client_of(app) as client:
        first = await post(client, run_input("r1", QUESTION))
        [interrupt] = first[-1]["outcome"]["interrupts"]
        # The client sends back the history of the last snapshot, reasoning messages included.
        history = of_type(first, "MESSAGES_SNAPSHOT")[-1]["messages"]
        assert "reasoning" in {m["role"] for m in history}
        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": OPTIONS[0]}]
        second = await post(client, run_input("r2", history, resume=resume))
        history = of_type(second, "MESSAGES_SNAPSHOT")[-1]["messages"]
        follow_up = {"id": "u2", "role": "user", "content": "Och för IT-drift Mindre?"}
        third = await post(client, run_input("r3", [*history, follow_up]))

    assert interrupt["metadata"]["langgraph"]["raw"] == question
    assert [e["messageId"] for e in of_type(first, "REASONING_START")] == ["rs_1"]
    for run in (second, third):
        assert of_type(run, "RUN_ERROR") == []
        assert of_type(run, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "verified"
    assert [e["messageId"] for e in of_type(second, "REASONING_START")] == ["rs_2"]
    assert [e["messageId"] for e in of_type(third, "REASONING_START")] == ["rs_3"]
    # Each reasoning item goes back once, before its call, with the summary it came with; the
    # history the client sent added nothing twice.
    assert items(fake.requests[1])[2:] == [
        ("reasoning", "rs_1"),
        ("function_call", "call_1"),
        ("function_call_output", "call_1"),
    ]
    assert items(fake.requests[2])[2:] == [
        ("reasoning", "rs_1"),
        ("function_call", "call_1"),
        ("function_call_output", "call_1"),
        ("reasoning", "rs_2"),
        ("function_call", "call_2"),
        ("function_call_output", "call_2"),
        ("message", "user"),
    ]
    [sent] = [item for item in fake.requests[2]["input"] if item.get("id") == "rs_1"]
    assert sent == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "".join(THINKING)}],
    }


@pytest.mark.anyio
async def test_after_a_failed_run_the_summary_the_client_kept_goes_back_once() -> None:
    # A run that fails (or that the user stops) has no MESSAGES_SNAPSHOT: the client keeps the
    # messages it built from the stream, the model's under the streamed id, not the
    # checkpoint's. Its reasoning message sent back must not reach OpenAI a second time.
    app, fake = app_on_fake_openai(
        [
            Reply([THINKING], "search_documents", SEARCH),
            Reply([[CHECKING]], "FinalAnswer", ANSWER),
        ],
        sessions=BreakingSessions(breaks={2}),
    )

    async with client_of(app) as client:
        failed = await post(client, run_input("r1", QUESTION))
        assert of_type(failed, "RUN_ERROR") != []
        assert of_type(failed, "MESSAGES_SNAPSHOT") == []
        [start] = of_type(failed, "TOOL_CALL_START")
        arguments = "".join(event["delta"] for event in of_type(failed, "TOOL_CALL_ARGS"))
        call = {
            "id": start["toolCallId"],
            "type": "function",
            "function": {"name": start["toolCallName"], "arguments": arguments},
        }
        kept = [
            *QUESTION,
            {"id": "rs_1", "role": "reasoning", "content": "".join(THINKING)},
            {"id": start["parentMessageId"], "role": "assistant", "toolCalls": [call]},
        ]
        follow_up = {"id": "u2", "role": "user", "content": "Försök igen."}
        answered = await post(client, run_input("r2", [*kept, follow_up]))

    assert of_type(answered, "RUN_ERROR") == []
    assert [i for i in items(fake.requests[1]) if i[0] == "reasoning"] == [("reasoning", "rs_1")]


def items(request: dict[str, Any]) -> list[tuple[str, str]]:
    """A request's input as (type, role or id): the conversation the model was sent."""
    found = []
    for item in request["input"]:
        kind = item.get("type", "message")
        name = item.get("role") if kind == "message" else item.get("call_id") or item.get("id")
        found.append((kind, str(name)))
    return found
