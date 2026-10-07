"""The agent's graph: create_agent with the citation check, the bounds and today's prompt.

What:
    `build_agent(model, tools, reader, checkpointer, settings)` compiles the
    agent `avtalsagent`: the model with avtal-mcp's tools and `ask_user`,
    the structured answer `FinalAnswer`, and the middleware around the
    loop. `AvtalAgent` is the compiled graph's type.

Why:
    One `create_agent` graph, the steps around the loop as middleware
    (ADR 0013): the web app sees the agent's steps while a run waits for
    the user, which an outer graph around the agent would hide. The answer
    is a tool call (ToolStrategy), not provider-structured output, which
    would stream the answer's JSON as chat text. A tool's error must reach
    the model so it can correct its call: avtal-mcp writes its errors for
    the model, in Swedish.

How:
    The middleware, in order:
    - `dated_system_prompt` sets the system prompt with today's date for
      every model call, so a process that runs for days (the API) never
      answers with the date it started on. Only the date's line changes.
    - `CitationCheck` resets the answer per question and checks the draft
      (`middleware.py`).
    - `ModelCallLimitMiddleware` ends a run after `AGENT_MODEL_CALL_LIMIT`
      model calls, new attempts included; the check turns the missing
      draft into `no_answer`. The count is not checkpointed, so a run
      resumed after `ask_user` counts from zero. (`create_agent` sets
      LangGraph's recursion limit itself.)
    - `AnswerOpenToolCalls` gives the model an error result for a tool
      call whose run broke off before the tool answered, so the
      conversation can go on (`open_tool_calls.py`).
    - `ToolErrorMiddleware` turns a `ToolException` that escapes a tool
      into a tool result with status error. langchain-mcp-adapters already
      does so for an MCP error result (its default `handle_tool_errors`),
      but `create_agent`'s ToolNode ends the run on a `ToolException` that
      a tool lets out (the adapter's with that option off, or any other
      tool's), so the graph does not depend on how the tools were loaded.
      Other exceptions (a broken MCP connection) still end the run.
    `ask_user` pauses the run in the checkpointer, so a graph that asks
    needs one; the command line uses memory, the API Postgres
    (`checkpointer.py`).
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    InputAgentState,
    ModelCallLimitMiddleware,
    ModelRequest,
    OutputAgentState,
    ToolCallRequest,
    ToolErrorMiddleware,
    dynamic_prompt,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, ToolException
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from avtalsagent.agent.ask_user import ask_user
from avtalsagent.agent.middleware import CitationCheck
from avtalsagent.agent.open_tool_calls import AnswerOpenToolCalls
from avtalsagent.agent.prompts import system_prompt, today_in_sweden
from avtalsagent.agent.schemas import AvtalState, FinalAnswer
from avtalsagent.agent.sections import SectionReader
from avtalsagent.config import Settings

AGENT_NAME = "avtalsagent"
# ToolStrategy's tool result for a draft; the citation check replaces it when the draft fails.
ANSWER_SUBMITTED = "Svaret är lämnat för kontroll."

AvtalAgent = CompiledStateGraph[
    AgentState[FinalAnswer], None, InputAgentState, OutputAgentState[FinalAnswer]
]


def build_agent(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    reader: SectionReader,
    checkpointer: BaseCheckpointSaver[str] | None,
    settings: Settings,
    *,
    today: Callable[[], date] = today_in_sweden,
) -> AvtalAgent:
    """The compiled agent; run it with `ainvoke` or `astream` (the check is async).

    `tools` are avtal-mcp's, `reader` reads the cited sections through the
    same MCP session, and `today` gives the date for the system prompt.
    """

    @dynamic_prompt
    def dated_system_prompt(request: ModelRequest[None]) -> str:
        return system_prompt(today())

    # The hooks' states differ, and AgentMiddleware is invariant in its state type.
    middleware: list[AgentMiddleware[Any, None]] = [
        dated_system_prompt,
        CitationCheck(reader, retries=settings.citation_retries),
        ModelCallLimitMiddleware(run_limit=settings.agent_model_call_limit, exit_behavior="end"),
        AnswerOpenToolCalls(),
        ToolErrorMiddleware(_tool_error_message),
    ]
    return create_agent(
        model,
        tools=[*tools, ask_user],
        response_format=ToolStrategy(FinalAnswer, tool_message_content=ANSWER_SUBMITTED),
        middleware=middleware,
        state_schema=AvtalState,
        checkpointer=checkpointer,
        name=AGENT_NAME,
    )


def _tool_error_message(error: Exception, request: ToolCallRequest) -> str | None:
    """A tool's own error as the text the model reads; None lets anything else through."""
    return str(error) if isinstance(error, ToolException) else None
