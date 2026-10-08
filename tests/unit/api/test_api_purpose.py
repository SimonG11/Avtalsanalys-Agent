"""Tests that the agent's `syfte` reaches the web app in the tool call's arguments, not avtal-mcp.

The agent's model is the one `make_agent_model` builds, on the fake OpenAI of
test_api_reasoning.py, which streams a call's arguments in several deltas as
OpenAI does. avtal-mcp's search_documents is the MCP adapter's tool on a
session that records what reaches the server. No network, key or database
is used.
"""

import json
from typing import cast

import pytest
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp import ClientSession
from mcp.types import Tool as MCPTool

from avtalsagent.agent.purpose import PURPOSE
from tests.unit.agent.test_purpose import FOUND, RecordingSession
from tests.unit.api.test_api_agui import QUESTION, Sessions, client_of, of_type, post, run_input
from tests.unit.api.test_api_reasoning import ANSWER, CHECKING, THINKING, Reply, app_on_fake_openai

REASON = "Jag letar efter uppsägningstiden i Allmänna villkor för IT-drift."
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_syfte_streams_first_in_the_steps_arguments_and_never_reaches_avtal_mcp() -> None:
    session = RecordingSession()
    search = convert_mcp_tool_to_langchain_tool(
        cast(ClientSession, session),
        MCPTool(name="search_documents", description="Sök.", inputSchema=SEARCH_SCHEMA),
    )
    arguments = {PURPOSE: REASON, "query": "uppsägningstid"}
    app, _ = app_on_fake_openai(
        [
            Reply([THINKING], "search_documents", arguments, argument_deltas=4),
            Reply([[CHECKING]], "FinalAnswer", ANSWER),
        ],
        sessions=Sessions([search]),
    )

    async with client_of(app) as client:
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "RUN_ERROR") == []
    [start, _] = of_type(events, "TOOL_CALL_START")
    assert start["toolCallName"] == "search_documents"
    deltas = [
        event["delta"]
        for event in of_type(events, "TOOL_CALL_ARGS")
        if event["toolCallId"] == start["toolCallId"]
    ]
    # The arguments arrive in parts, syfte first, so the web app can show it before the rest.
    assert len(deltas) == 4
    assert deltas[0].startswith(f'{{"{PURPOSE}": "')
    assert json.loads("".join(deltas)) == arguments
    # avtal-mcp got its own arguments only, and the step its result.
    assert session.calls == [("search_documents", {"query": "uppsägningstid"})]
    [result] = [
        event
        for event in of_type(events, "TOOL_CALL_RESULT")
        if event["toolCallId"] == start["toolCallId"]
    ]
    assert FOUND in result["content"]
    # The snapshot's assistant message keeps syfte in the call's arguments.
    messages = of_type(events, "MESSAGES_SNAPSHOT")[-1]["messages"]
    calls = [call for m in messages if m["role"] == "assistant" for call in m.get("toolCalls", [])]
    assert json.loads(calls[0]["function"]["arguments"]) == arguments
