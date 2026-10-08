"""The agent over AG-UI: `POST /agui`, which the web app runs the agent through.

What:
    `AvtalAguiAgent`, ag-ui-langgraph's `LangGraphAgent` fitted to the web
    app's contract; `AgentRuns`, which opens what one run needs and gives
    the agent; and `router` with `POST /agui` (one run, streamed as
    server-sent events) and `GET /agui/health`.

Why:
    ag-ui-langgraph turns LangGraph's events into AG-UI's, which CopilotKit
    reads (ADR 0010). Seven of its defaults do not fit the contract
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
    - Several `ask_user` questions can wait at once (parallel calls in one
      model message). The library resumes them with a key of its own
      (`__agui_resume_map__`), or a lone answer as the answer itself, but
      with more than one waiting LangGraph takes only a map from interrupt
      id to answer: the run failed, and each new message got the same
      questions back. With more than one waiting, the answers go by id, as
      on the command line (`agent/__main__.py`); a cancelled one as
      `ask_user`'s cancel mark. An answer to a question that is not waiting
      is dropped, and a question left without an answer ends the run
      again. Once one of them is answered, one question waits, and there
      the library reads no ids: the first answer sent again would go to
      the other question, and both sent again would give `ask_user` the
      library's map as text. So with one question waiting, of several
      answers only its own goes on, alone, the library's way, and a lone
      answer with another id in LangGraph's form (32 hex digits) is
      dropped; with no answer left, LangGraph resumes nothing, and the run
      ends with the question again. A lone answer with the waiting
      question's id, or with an id not in that form, goes the library's
      way, as for an ordinary single question.
    - A RAW copy of every LangGraph event is off: nothing reads it.
    - Each run's config is filled in with LangChain's defaults, and their
      recursion limit (25 of LangGraph's steps) replaced the graph's own
      (`create_agent`'s 9 999). With the middleware a model call is about
      four steps, so a question that needed more than about six model
      calls failed in the web app, though not on the command line. The
      agent's config sets the graph's own limit again;
      `ModelCallLimitMiddleware` is what bounds a run (`agent/graph.py`).
    - The client's `state` and `forwardedProps.node_name` are dropped.
      With `node_name`, the library's "continue" mode writes the client's
      state into the graph as that node, past the input schema, and goes
      on from there: a client could set `answer` (or the check's private
      `fallback_answer` and `validation_retries`) and skip the check. The
      web app sends neither; the graph's input is the messages alone.
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
    on its tools with the shared model, reviewer, checkpointer and store of
    the user's files (`build_agent`; the graph reads the files of the run's
    thread, which ag-ui-langgraph puts in the config as `thread_id`),
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

from ag_ui.core import (
    EventType,
    Interrupt,
    ResumeEntry,
    RunAgentInput,
    RunErrorEvent,
    RunStartedEvent,
)
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.agent import ProcessedEvents
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.pregel._utils import is_xxh3_128_hexdigest  # LangGraph's test of an interrupt id
from langgraph.types import Command

from avtalsagent.agent.ask_user import CANCELLED_MARK
from avtalsagent.agent.graph import AGENT_NAME, AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.config import Settings
from avtalsagent.observability.tracing import OFF as TRACING_OFF
from avtalsagent.observability.tracing import Tracing
from avtalsagent.uploads.store import UploadStore
from avtalsagent.validation.review import AnswerReviewer

_log = logging.getLogger(__name__)

RUN_FAILED = (
    "Agenten kunde inte svara på grund av ett fel i tjänsten. Försök igen om en stund; "
    "felet finns i API:ets logg."
)

router = APIRouter()

OpenTools = Callable[[Settings], AbstractAsyncContextManager[McpTools]]


class AvtalAguiAgent(LangGraphAgent):
    """`LangGraphAgent` with its snapshots, errors, client state and resumes fitted to the API.

    A snapshot holds only the answer, an error names no internals, the client's state is
    dropped, and the answers to waiting `ask_user` questions go to them by id where an id names
    a question (`_build_command_from_agui_resume`).
    """

    def get_state_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"answer": state.get("answer")}

    def _build_command_from_agui_resume(
        self, entries: list[ResumeEntry], *, open_interrupts: list[Interrupt] | None = None
    ) -> Command[Any]:
        """The answers to the waiting questions as LangGraph takes them.

        Several questions waiting: a map from interrupt id to answer, without
        the answers to questions that do not wait. One question waiting:
        - one answer, with its id or an id not in LangGraph's form (32 hex
          digits): the library's command, unchanged;
        - one answer with another id in LangGraph's form (an earlier
          question's, sent again): an empty map;
        - several answers: its own alone, as the library sends one answer,
          or an empty map when none is its own.
        An empty map resumes nothing, and the run ends with the question
        again.
        """
        open_ids = {interrupt.id for interrupt in open_interrupts or []}
        answers = {entry.interrupt_id: entry for entry in entries}  # the last answer per id
        if len(open_ids) > 1:
            # An answer to a question that is not waiting is dropped: LangGraph refuses the whole
            # map over a key not in an interrupt id's form. When none is left, the map is empty:
            # LangGraph resumes no question, and the run ends with the same ones again.
            return Command(
                resume={
                    id_: _resume_value(entry) for id_, entry in answers.items() if id_ in open_ids
                }
            )
        if len(open_ids) == 1:
            # The library reads no ids here: it would send several answers as its own map, which
            # ask_user would read as text, and a lone answer to the waiting question, whatever
            # its id. An empty map resumes nothing: the run ends with the question again.
            (waiting,) = open_ids
            if len(entries) > 1:
                if waiting not in answers:
                    return Command(resume={})
                return super()._build_command_from_agui_resume(
                    [answers[waiting]], open_interrupts=open_interrupts
                )
            if len(entries) == 1 and _another_questions_answer(entries[0], waiting):
                return Command(resume={})
        return super()._build_command_from_agui_resume(entries, open_interrupts=open_interrupts)

    async def run(self, input: RunAgentInput) -> AsyncGenerator[ProcessedEvents, None]:
        async for event in super().run(_without_client_state(input)):
            if event.type == EventType.RUN_ERROR:
                # The library has logged the exception; the browser gets the fixed text.
                event = RunErrorEvent(type=EventType.RUN_ERROR, message=RUN_FAILED)
            yield event


def _resume_value(entry: ResumeEntry) -> Any:
    """What `ask_user` gets back for one answer: the answer, or the cancel mark it reads."""
    return entry.payload if entry.status == "resolved" else {CANCELLED_MARK: True}


def _another_questions_answer(entry: ResumeEntry, waiting: str) -> bool:
    """Whether `entry` names a question other than `waiting` by an id in LangGraph's form."""
    return entry.interrupt_id != waiting and is_xxh3_128_hexdigest(entry.interrupt_id)


def _without_client_state(input: RunAgentInput) -> RunAgentInput:
    """`input` without the client's state and the key that would write it into the graph."""
    props = input.forwarded_props
    if isinstance(props, dict):
        # The library reads node_name in any spelling it turns into snake case (nodeName).
        props = {k: v for k, v in props.items() if k.replace("_", "").lower() != "nodename"}
    return input.model_copy(update={"state": {}, "forwarded_props": props})


def make_agui_agent(graph: AvtalAgent, config: RunnableConfig | None = None) -> AvtalAguiAgent:
    """The agent for AG-UI, with the settings the module docstring explains.

    `config` is merged into every run's config: the tracing's callbacks and metadata.
    """
    return AvtalAguiAgent(
        name=AGENT_NAME,
        graph=graph,
        config={**(config or {}), "recursion_limit": recursion_limit(graph)},
        enable_legacy_on_interrupt_event=False,
        emit_interrupt_outcome=True,
        emit_raw_events=False,
    )


def recursion_limit(graph: AvtalAgent) -> int:
    """The graph's own recursion limit, which `create_agent` sets, for each run's config."""
    limit = (graph.config or {}).get("recursion_limit")
    if not isinstance(limit, int):
        raise TypeError("the agent's graph has no recursion limit of its own")
    return limit


class AgentRuns:
    """What every run shares (the models, the checkpointer), and a new MCP session per run."""

    def __init__(
        self,
        settings: Settings,
        model: BaseChatModel,
        reviewer: AnswerReviewer,
        checkpointer: BaseCheckpointSaver[str],
        open_tools: OpenTools,
        tracing: Tracing = TRACING_OFF,
        uploads: UploadStore | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._reviewer = reviewer
        self._checkpointer = checkpointer
        self._open_tools = open_tools
        self._tracing = tracing
        self._uploads = uploads

    @asynccontextmanager
    async def open(self, thread_id: str | None = None) -> AsyncIterator[AvtalAguiAgent]:
        """The agent for one run, on a session to avtal-mcp that closes with the block.

        With tracing on, the run is a trace in the conversation's session (`thread_id`).
        """
        async with self._open_tools(self._settings) as mcp:
            graph = build_agent(
                self._model,
                mcp,
                self._reviewer,
                self._checkpointer,
                self._settings,
                uploads=self._uploads,
            )
            trace = self._tracing.run_config(name="fråga", session_id=thread_id, tags=["api"])
            yield make_agui_agent(graph, trace)


@router.post("/agui")
async def run_agent(input_data: RunAgentInput, request: Request) -> StreamingResponse:
    """One run of the agent, its AG-UI events streamed as server-sent events."""
    runs: AgentRuns = request.app.state.runs
    encoder = EventEncoder(accept=request.headers.get("accept", ""))

    async def events() -> AsyncGenerator[str, None]:
        started = ended = False
        try:
            async with runs.open(input_data.thread_id) as agent:
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
