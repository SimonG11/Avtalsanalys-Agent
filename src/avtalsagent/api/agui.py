"""The agent over AG-UI: `POST /agui`, which the web app runs the agent through.

What:
    `AvtalAguiAgent`, ag-ui-langgraph's `LangGraphAgent` fitted to the web
    app's contract; `AgentRuns`, which opens what one run needs and gives
    the agent; and `router` with `POST /agui` (one run, streamed as
    server-sent events) and `GET /agui/health`.

Why:
    ag-ui-langgraph turns LangGraph's events into AG-UI's, which CopilotKit
    reads (ADR 0010). Four of its defaults do not fit the contract
    (webbapp-kontrakt.md) or the API:
    - A state snapshot holds the whole state again with every step: every
      message, and the draft before its check (`structured_response`).
      The web app reads only `answer` (clarification 2), so a snapshot is
      `{"answer": ...}` alone.
    - A failed run's RUN_ERROR carries the exception's text to the
      browser, which can name an address or quote a database error. The
      browser gets a fixed Swedish text; the library logs the exception,
      and the API's log removes the secrets from it (`__main__.py`).
    - An `ask_user` question ends the run with the AG-UI standard's
      interrupt outcome on RUN_FINISHED (clarification 1). The older CUSTOM
      `on_interrupt` event, which the library also sends by default, is
      off: the web app reads the outcome (the web thread confirmed this
      2026-10-07, and its tests run that shape alone).
    - A RAW copy of every LangGraph event is off: nothing reads it.
    Each run gets its own session to avtal-mcp. Over HTTP a call that
    fails (avtal-mcp restarting, a timeout) ends its session as well as
    the run (ADR 0013); a session shared by all runs would then fail every
    question until the API restarted. avtal-mcp keeps no state between
    calls, so a new session costs a handshake and the tool list, and the
    conversation itself lives in the checkpointer, which all runs share.
    The route is the library's `add_langgraph_fastapi_endpoint`, written
    out, since the agent is made per run.

How:
    `AgentRuns.open()` opens a session (`open_mcp_tools`), builds the graph
    on its tools with the shared model and checkpointer (`build_agent`),
    yields the AG-UI agent and closes the session when the run's stream
    ends. Each request gets its own agent object: the library keeps a
    run's progress on the instance. The body is AG-UI's `RunAgentInput`
    (FastAPI answers 422 when it does not fit). If the session cannot be
    opened, the stream is RUN_STARTED and RUN_ERROR with the fixed text,
    as for any failed run, and the log says why. If it fails during the
    run, the MCP client cancels the run (the library sends nothing then),
    and the route ends the stream with RUN_ERROR itself, so the web app
    never waits on a stream that just stops. A new message while an
    `ask_user` question waits does not run the graph: the library sends
    the waiting question again, so the user answers it first (ADR 0014).
"""

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from ag_ui.core import EventType, RunAgentInput, RunErrorEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.agent import ProcessedEvents
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from avtalsagent.agent.graph import AGENT_NAME, AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.config import Settings

_log = logging.getLogger(__name__)

RUN_FAILED = (
    "Agenten kunde inte svara på grund av ett fel i tjänsten. Försök igen om en stund; "
    "felet finns i API:ets logg."
)

router = APIRouter()

OpenTools = Callable[[Settings], AbstractAsyncContextManager[McpTools]]


class AvtalAguiAgent(LangGraphAgent):
    """`LangGraphAgent` whose snapshots hold only the answer and whose errors name no internals."""

    def get_state_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"answer": state.get("answer")}

    async def run(self, input: RunAgentInput) -> AsyncGenerator[ProcessedEvents, None]:
        async for event in super().run(input):
            if event.type == EventType.RUN_ERROR:
                # The library has logged the exception; the browser gets the fixed text.
                event = RunErrorEvent(type=EventType.RUN_ERROR, message=RUN_FAILED)
            yield event


def make_agui_agent(graph: AvtalAgent) -> AvtalAguiAgent:
    """The agent for AG-UI, with the settings the module docstring explains."""
    return AvtalAguiAgent(
        name=AGENT_NAME,
        graph=graph,
        enable_legacy_on_interrupt_event=False,
        emit_interrupt_outcome=True,
        emit_raw_events=False,
    )


class AgentRuns:
    """What every run shares (the model, the checkpointer), and a new MCP session per run."""

    def __init__(
        self,
        settings: Settings,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[str],
        open_tools: OpenTools,
    ) -> None:
        self._settings = settings
        self._model = model
        self._checkpointer = checkpointer
        self._open_tools = open_tools

    @asynccontextmanager
    async def open(self) -> AsyncIterator[AvtalAguiAgent]:
        """The agent for one run, on a session to avtal-mcp that closes with the block."""
        async with self._open_tools(self._settings) as mcp:
            graph = build_agent(
                self._model, mcp.tools, mcp.reader, self._checkpointer, self._settings
            )
            yield make_agui_agent(graph)


@router.post("/agui")
async def run_agent(input_data: RunAgentInput, request: Request) -> StreamingResponse:
    """One run of the agent, its AG-UI events streamed as server-sent events."""
    runs: AgentRuns = request.app.state.runs
    encoder = EventEncoder(accept=request.headers.get("accept", ""))

    async def events() -> AsyncGenerator[str, None]:
        started = ended = False
        try:
            async with runs.open() as agent:
                async for event in agent.run(input_data):
                    started = started or event.type == EventType.RUN_STARTED
                    ended = ended or event.type in RUN_ENDS
                    yield encoder.encode(event)
        except Exception:
            # The run reports its own errors as RUN_ERROR. This is the session to avtal-mcp:
            # it failed to open, it failed during the run and cancelled it (the library then
            # sends nothing), or it failed to close after the run had ended.
            _log.exception("run %s: the session to avtal-mcp failed", input_data.run_id)
            if not ended:
                for event in failed_run(input_data, started=started):
                    yield encoder.encode(event)

    return StreamingResponse(events(), media_type=encoder.get_content_type())


# The events that end a run in AG-UI; a stream without one leaves the web app waiting.
RUN_ENDS = frozenset({EventType.RUN_FINISHED, EventType.RUN_ERROR})


def failed_run(input_data: RunAgentInput, *, started: bool = False) -> list[ProcessedEvents]:
    """The end of a failed run as AG-UI ends one: RUN_STARTED (unless sent), then RUN_ERROR."""
    start = RunStartedEvent(
        type=EventType.RUN_STARTED, thread_id=input_data.thread_id, run_id=input_data.run_id
    )
    error = RunErrorEvent(type=EventType.RUN_ERROR, message=RUN_FAILED)
    return [error] if started else [start, error]


@router.get("/agui/health")
def agent_health() -> dict[str, Any]:
    """The library's health check, which the web app's mock also answers."""
    return {"status": "ok", "agent": {"name": AGENT_NAME}}
