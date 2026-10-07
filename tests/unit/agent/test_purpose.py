"""Tests for avtalsagent.agent.purpose: the agent's `syfte` on each avtal-mcp tool call.

The graph runs offline as in test_agent_graph.py, with a scripted model that
also records the tool schemas it is bound with. avtal-mcp's tools are made
by the MCP adapter, on a session that records what reaches the server, or on
the in-memory stand-in server of test_mcp_tools.py, whose tools refuse an
argument they do not know.
"""

from typing import Any, cast

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from langgraph.checkpoint.memory import InMemorySaver
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import Field

from avtalsagent.agent.ask_user import ask_user
from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools, load_tools
from avtalsagent.agent.purpose import (
    PURPOSE,
    PURPOSE_DESCRIPTION,
    PURPOSE_MAX_CHARS,
    purpose_problem,
    with_purpose,
)
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import (
    PURPOSE_TEXT,
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)
from tests.unit.agent.test_agent_graph import GOOD, QUESTION, SECTION, SHA, THREAD
from tests.unit.agent.test_mcp_tools import SECTION as SERVED
from tests.unit.agent.test_mcp_tools import stand_in_server

READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sha256": {"type": "string", "description": "Filens hash."},
        "section_number": {"type": "string"},
    },
    "required": ["sha256"],
    "additionalProperties": False,
}
FOUND = f"Avsnitt 6.21.9 Uppsägning, sha256 {SHA}, section_position 41."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class SchemaModel(ScriptedModel):
    """A scripted model that also records the tool schemas of each `bind_tools`."""

    schemas: list[dict[str, dict[str, Any]]] = Field(default_factory=list)

    def bind_tools(
        self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, Any]:
        functions = [convert_to_openai_tool(tool)["function"] for tool in tools]
        self.schemas.append({function["name"]: function for function in functions})
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)


class RecordingSession:
    """An MCP client session that records each call's arguments and answers with a hit."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=FOUND)])


def read_section_on(session: RecordingSession) -> BaseTool:
    """avtal-mcp's read_section as the adapter makes it, on `session`."""
    return convert_mcp_tool_to_langchain_tool(
        cast(ClientSession, session),
        MCPTool(name="read_section", description="Läs ett avsnitt.", inputSchema=READ_SCHEMA),
    )


def build(script: list[Any], tools: list[BaseTool]) -> tuple[AvtalAgent, SchemaModel]:
    model = SchemaModel(script=script)
    mcp = McpTools(
        tools=tools,
        reader=DictReader([SECTION]),
        register=ListRegister(),
        amendments=DictAmendments(),
    )
    settings = Settings(_env_file=None, validation_retries=1, agent_model_call_limit=8)
    graph = build_agent(model, mcp, ScriptedReviewer(), InMemorySaver(serde=serializer()), settings)
    return graph, model


def read_call(call_id: str, **args: Any) -> Any:
    return tool_call("read_section", {"sha256": SHA, "section_number": "6.21.9", **args}, call_id)


# --- the schema the model sees -------------------------------------------------------


@pytest.mark.anyio
async def test_the_model_sees_syfte_first_and_required_on_each_avtal_mcp_tool() -> None:
    graph, model = build(
        [final_answer("Det framgår inte.", answered=False, call_id="c1")],
        [read_section_on(RecordingSession())],
    )

    await graph.ainvoke(QUESTION, THREAD)

    [bound] = model.schemas
    parameters = bound["read_section"]["parameters"]
    assert list(parameters["properties"]) == [PURPOSE, "sha256", "section_number"]
    assert parameters["properties"][PURPOSE] == {
        "type": "string",
        "description": PURPOSE_DESCRIPTION,
        "minLength": 1,
        "maxLength": PURPOSE_MAX_CHARS,
    }
    assert parameters["required"] == [PURPOSE, "sha256"]
    # The rest of avtal-mcp's schema, and the tool's name and description, are as they were.
    assert parameters["properties"]["sha256"] == READ_SCHEMA["properties"]["sha256"]
    assert parameters["additionalProperties"] is False
    assert bound["read_section"]["description"] == "Läs ett avsnitt."
    # ask_user's question is shown as it is, and the answer is not a step: no syfte.
    assert PURPOSE not in bound[ask_user.name]["parameters"]["properties"]
    assert PURPOSE not in bound["FinalAnswer"]["parameters"]["properties"]


def test_the_description_says_what_the_sentence_is_and_who_reads_it() -> None:
    assert PURPOSE_DESCRIPTION == (
        "En kort mening om vad du vill ta reda på med anropet och varför. Användaren ser den "
        "som din tanke."
    )
    assert PURPOSE_MAX_CHARS == 200


def test_a_tool_with_a_pydantic_schema_gets_syfte_too() -> None:
    offered = convert_to_openai_tool(with_purpose(ask_user))["function"]["parameters"]

    assert list(offered["properties"]) == [PURPOSE, "question", "options"]
    assert offered["required"][0] == PURPOSE


def test_a_tool_that_already_has_a_syfte_is_refused_when_the_graph_is_built() -> None:
    schema = {"type": "object", "properties": {PURPOSE: {"type": "string"}}}
    tool = convert_mcp_tool_to_langchain_tool(
        cast(ClientSession, RecordingSession()),
        MCPTool(name="odd_tool", description="Udda.", inputSchema=schema),
    )

    with pytest.raises(ValueError, match="already has an argument 'syfte'"):
        with_purpose(tool)


@pytest.mark.anyio
async def test_the_tools_the_baseline_calls_keep_avtal_mcps_schema() -> None:
    # The fixed-workflow baseline calls McpTools.tools itself: building the graph on them
    # changes nothing in them.
    async with create_connected_server_and_client_session(stand_in_server()) as session:
        mcp = await load_tools(session)
        before = [convert_to_openai_tool(tool) for tool in mcp.tools]
        build([], mcp.tools)

        after = [convert_to_openai_tool(tool) for tool in mcp.tools]
        assert after == before
        for tool in mcp.tools:
            assert isinstance(tool.args_schema, dict)
            assert PURPOSE not in tool.args_schema["properties"], tool.name


# --- the call ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_syfte_is_removed_before_the_call_reaches_avtal_mcp() -> None:
    session = RecordingSession()
    graph, model = build(
        [read_call("c1"), final_answer("Tre månader [1].", [GOOD], call_id="c2")],
        [read_section_on(session)],
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert session.calls == [("read_section", {"sha256": SHA, "section_number": "6.21.9"})]
    [found] = [
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "c1"
    ]
    assert (found.status, found.text) == ("success", FOUND)
    # The model's call keeps its syfte, which the web app shows and the model reads again.
    [call] = [c for m in result["messages"] if isinstance(m, AIMessage) for c in m.tool_calls][:1]
    assert (call["id"], call["args"][PURPOSE]) == ("c1", PURPOSE_TEXT)
    sent = [m for m in model.calls[1] if isinstance(m, AIMessage)]
    assert sent[0].tool_calls[0]["args"][PURPOSE] == PURPOSE_TEXT
    assert result["answer"].status == "verified"


@pytest.mark.anyio
async def test_the_stand_in_server_answers_a_call_that_carried_syfte() -> None:
    # avtal-mcp's tools are closed (additionalProperties false): syfte would be refused.
    async with create_connected_server_and_client_session(stand_in_server()) as session:
        mcp = await load_tools(session)
        served = {"sha256": SERVED.sha256, "section_position": SERVED.section_position}
        refused = await session.call_tool("read_section", {PURPOSE: PURPOSE_TEXT, **served})
        graph, _ = build(
            [
                tool_call("read_section", served, "c1"),
                final_answer("Det framgår inte.", answered=False, call_id="c2"),
            ],
            mcp.tools,
        )

        result = await graph.ainvoke(QUESTION, THREAD)

    assert refused.isError
    assert PURPOSE in " ".join(b.text for b in refused.content if isinstance(b, TextContent))
    [read] = [
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "c1"
    ]
    assert read.status == "success"


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({}, "syfte saknas"),
        ({PURPOSE: "   "}, "syfte är tomt"),
        ({PURPOSE: 7}, "syfte ska vara text"),
        ({PURPOSE: "x" * (PURPOSE_MAX_CHARS + 1)}, "syfte har 201 tecken"),
    ],
    ids=["missing", "blank", "not-text", "too-long"],
)
@pytest.mark.anyio
async def test_a_call_without_a_good_syfte_is_an_error_the_model_corrects(
    args: dict[str, Any], problem: str
) -> None:
    session = RecordingSession()
    graph, model = build(
        [
            tool_call("read_section", {"sha256": SHA, **args}, "c1", purpose=None),
            read_call("c2"),
            final_answer("Tre månader [1].", [GOOD], call_id="c3"),
        ],
        [read_section_on(session)],
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    refusal = model.calls[1][-1]
    assert isinstance(refusal, ToolMessage)
    assert (refusal.tool_call_id, refusal.name, refusal.status) == ("c1", "read_section", "error")
    assert refusal.text == (
        f"Anropet nekades: {problem}. Ange i syfte en kort mening, högst 200 tecken, om vad du "
        "vill ta reda på med anropet och varför, och anropa read_section igen."
    )
    # The refused call never reached avtal-mcp; the corrected one did, and the run went on.
    assert session.calls == [("read_section", {"sha256": SHA, "section_number": "6.21.9"})]
    assert result["answer"].status == "verified"


def test_a_syfte_of_exactly_the_most_characters_passes() -> None:
    assert purpose_problem({PURPOSE: "x" * PURPOSE_MAX_CHARS}) is None
    assert purpose_problem({PURPOSE: f"  {'x' * PURPOSE_MAX_CHARS}  "}) is None


@pytest.mark.anyio
async def test_ask_user_and_final_answer_take_no_syfte() -> None:
    graph, _ = build(
        [
            tool_call(
                "ask_user", {"question": "Vilket avtal?", "options": ["Större", "Mindre"]}, "c1"
            )
        ],
        [read_section_on(RecordingSession())],
    )

    await graph.ainvoke(QUESTION, THREAD)

    state = await graph.aget_state(THREAD)
    [interrupt] = state.interrupts
    assert interrupt.value == {"question": "Vilket avtal?", "options": ["Större", "Mindre"]}
