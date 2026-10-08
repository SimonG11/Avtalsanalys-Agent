"""The agent's graph: create_agent with the answer check, the bounds and today's prompt.

What:
    `build_agent(model, mcp, reviewer, checkpointer, settings)` compiles
    the agent `avtalsagent`: the model with avtal-mcp's tools and
    `ask_user`, the structured answer `FinalAnswer`, and the middleware
    around the loop. With `uploads`, a store of the user's own files, it
    also has `list_uploads` and `read_upload`. `AvtalAgent` is the compiled
    graph's type.

Why:
    One `create_agent` graph, the steps around the loop as middleware
    (ADR 0013): the web app sees the agent's steps while a run waits for
    the user, which an outer graph around the agent would hide. The answer
    is a tool call (ToolStrategy), not provider-structured output, which
    would stream the answer's JSON as chat text. A tool's error must reach
    the model so it can correct its call: avtal-mcp writes its errors for
    the model, in Swedish. The user's own files are read by tools in the
    graph, not in avtal-mcp, which is read-only over the shared corpus and
    never sees them (ADR 0026); without a store (the command line without
    `--fil`, the measurement, the tests) the graph is exactly as before.

How:
    The middleware, in order:
    - `dated_system_prompt` sets the system prompt with today's date for
      every model call, so a process that runs for days (the API) never
      answers with the date it started on. Only the date's line changes.
    - `UploadPrompt`, with a store only, names the conversation's files
      after the prompt, or takes the upload tools out of a call whose
      conversation has none, so its prompt and tools are as without a
      store (`upload_prompt.py`).
    - `AnswerCheck` resets the answer per question and checks the draft:
      the citations, the register facts and the latest wording through
      avtal-mcp's session, then the reviewer (`middleware.py`,
      `validation/chain.py`). With a store, a cited section of the
      conversation's file is read from the store and has no amendments
      (`upload_readers.py`).
    - `ModelCallLimitMiddleware` ends a run after `AGENT_MODEL_CALL_LIMIT`
      model calls, new attempts included; the check then gives the answer
      the last failed draft would have got (`fallback_answer`), or
      `no_answer` when no draft failed. The count is not checkpointed, so
      a run resumed after `ask_user` counts from zero. (`create_agent`
      sets LangGraph's recursion limit itself, to 9 999; the API passes it
      on to each run, since ag-ui-langgraph would otherwise put
      LangChain's default of 25 in its place, `api/agui.py`.)
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

from collections.abc import Callable
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

from avtalsagent.agent.amendments import AmendmentReader
from avtalsagent.agent.ask_user import ask_user
from avtalsagent.agent.list_uploads import make_list_uploads
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.middleware import AnswerCheck
from avtalsagent.agent.open_tool_calls import AnswerOpenToolCalls
from avtalsagent.agent.prompts import system_prompt, today_in_sweden
from avtalsagent.agent.read_upload import make_read_upload
from avtalsagent.agent.schemas import AvtalState, FinalAnswer
from avtalsagent.agent.sections import SectionReader
from avtalsagent.agent.upload_prompt import UploadPrompt
from avtalsagent.agent.upload_readers import UploadAmendmentReader, UploadSectionReader
from avtalsagent.config import Settings
from avtalsagent.uploads.store import UploadStore
from avtalsagent.validation.review import AnswerReviewer

AGENT_NAME = "avtalsagent"
# ToolStrategy's tool result for a draft; the answer check replaces it when the draft fails.
ANSWER_SUBMITTED = "Svaret är lämnat för kontroll."

AvtalAgent = CompiledStateGraph[
    AgentState[FinalAnswer], None, InputAgentState, OutputAgentState[FinalAnswer]
]


def build_agent(
    model: BaseChatModel,
    mcp: McpTools,
    reviewer: AnswerReviewer,
    checkpointer: BaseCheckpointSaver[str] | None,
    settings: Settings,
    *,
    today: Callable[[], date] = today_in_sweden,
    uploads: UploadStore | None = None,
) -> AvtalAgent:
    """The compiled agent; run it with `ainvoke` or `astream` (the check is async).

    `mcp` holds avtal-mcp's tools and the readers of sections, register
    rows and amendments on the same session, `reviewer` reviews a draft
    that passed the deterministic rules, and `today` gives the date for the
    system prompt and the check. `uploads` holds the user's own files, read
    by the run's thread; without it the graph has no upload tools.
    """

    @dynamic_prompt
    def dated_system_prompt(request: ModelRequest[None]) -> str:
        return system_prompt(today())

    tools: list[BaseTool] = [*mcp.tools, ask_user]
    reader: SectionReader = mcp.reader
    amendments: AmendmentReader = mcp.amendments
    # The hooks' states differ, and AgentMiddleware is invariant in its state type.
    prompts: list[AgentMiddleware[Any, None]] = [dated_system_prompt]
    if uploads is not None:
        tools += [make_list_uploads(uploads), make_read_upload(uploads)]
        reader = UploadSectionReader(uploads, mcp.reader)
        amendments = UploadAmendmentReader(uploads, mcp.amendments)
        prompts.append(UploadPrompt(uploads))
    middleware: list[AgentMiddleware[Any, None]] = [
        *prompts,
        AnswerCheck(
            reader,
            mcp.register,
            amendments,
            reviewer,
            retries=settings.validation_retries,
            today=today,
        ),
        ModelCallLimitMiddleware(run_limit=settings.agent_model_call_limit, exit_behavior="end"),
        AnswerOpenToolCalls(),
        ToolErrorMiddleware(_tool_error_message),
    ]
    return create_agent(
        model,
        tools=tools,
        response_format=ToolStrategy(FinalAnswer, tool_message_content=ANSWER_SUBMITTED),
        middleware=middleware,
        state_schema=AvtalState,
        checkpointer=checkpointer,
        name=AGENT_NAME,
    )


def _tool_error_message(error: Exception, request: ToolCallRequest) -> str | None:
    """A tool's own error as the text the model reads; None lets anything else through."""
    return str(error) if isinstance(error, ToolException) else None
