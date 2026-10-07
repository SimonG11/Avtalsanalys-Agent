"""The API's FastAPI app: the agent over AG-UI, the cited PDFs, the user's files and health.

What:
    `create_app(settings, ...)` builds the app with the routes of `agui.py`
    (`POST /agui`, `GET /agui/health`), `documents.py`
    (`GET /api/documents/{sha256}/pdf`), `uploads.py` (`/api/uploads`) and
    `GET /health`.

Why:
    Some of what the agent needs stays open while the app runs: the model
    clients (the agent's and the reviewer's), the checkpointer's Postgres
    pool, for the PDF route a read-only database engine, and the store of
    the user's files. They are opened in the app's lifespan, once,
    and closed in reverse order when the app stops. The session to
    avtal-mcp is opened per run instead (`agui.AgentRuns`), so a failed
    call cannot break the questions after it. Starting checks the
    configuration all the same: a missing key, or an avtal-mcp that cannot
    be reached, stops the start with an error in the log instead of failing
    the first question. The way each part is opened is a parameter, so the
    tests run the whole app with a scripted model, tools in memory, an
    in-memory checkpointer and no database.

How:
    The lifespan makes the model clients first, the agent's and the
    reviewer's (`make_reviewer`); without OPENAI_API_KEY, `make_agent_model`
    stops the start before any connection is opened. Then it opens a
    session to avtal-mcp once and closes it (`open_mcp_tools`;
    MCP_TRANSPORT=streamable_http in the container), and opens the
    checkpointer (`open_checkpointer`; CHECKPOINTER=postgres in the
    container) and the document lookup, then the upload store
    (`open_upload_store`; UPLOAD_STORE=postgres in the container, which
    creates its tables and deletes expired files), after the tracing
    (`open_tracing`, off without Langfuse's keys), which closes last and so
    sends what is left. It puts the settings, `AgentRuns`, the lookup and
    the upload store on `app.state`, where the routes read them. `GET /health` answers
    without touching the database or avtal-mcp: it says the process serves
    HTTP, which is what a container's health check asks.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    AsyncExitStack,
    asynccontextmanager,
    contextmanager,
)

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from avtalsagent.agent.checkpointer import open_checkpointer
from avtalsagent.agent.mcp_tools import open_mcp_tools
from avtalsagent.agent.model import make_agent_model
from avtalsagent.agent.reviewer import make_reviewer
from avtalsagent.api.agui import AgentRuns, OpenTools
from avtalsagent.api.agui import router as agui_router
from avtalsagent.api.documents import DatabaseDocumentFiles, DocumentFiles
from avtalsagent.api.documents import router as documents_router
from avtalsagent.api.uploads import router as uploads_router
from avtalsagent.config import Settings, get_settings
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.observability.tracing import Tracing, open_tracing
from avtalsagent.uploads.open_store import open_upload_store
from avtalsagent.uploads.store import UploadStore
from avtalsagent.validation.review import AnswerReviewer

OpenCheckpointer = Callable[[Settings], AbstractAsyncContextManager[BaseCheckpointSaver[str]]]
OpenDocuments = Callable[[Settings], AbstractContextManager[DocumentFiles]]
OpenTracing = Callable[[Settings], AbstractContextManager[Tracing]]
OpenUploads = Callable[[Settings], AbstractAsyncContextManager[UploadStore]]


@contextmanager
def open_document_files(settings: Settings) -> Iterator[DocumentFiles]:
    """The document lookup on a read-only engine, disposed of when the block ends."""
    engine = create_db_engine(str(settings.database_url), read_only=True)
    try:
        yield DatabaseDocumentFiles(session_factory(engine), settings.data_dir)
    finally:
        engine.dispose()


def create_app(
    settings: Settings | None = None,
    *,
    make_model: Callable[[Settings], BaseChatModel] = make_agent_model,
    make_answer_reviewer: Callable[[Settings], AnswerReviewer] = make_reviewer,
    open_tools: OpenTools = open_mcp_tools,
    open_saver: OpenCheckpointer = open_checkpointer,
    open_documents: OpenDocuments = open_document_files,
    open_trace: OpenTracing = open_tracing,
    open_uploads: OpenUploads = open_upload_store,
) -> FastAPI:
    """The API; the agent and its connections open when the app starts (see the module)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        current = settings or get_settings()
        model = make_model(current)
        reviewer = make_answer_reviewer(current)
        async with open_tools(current):  # avtal-mcp answers; each run opens its own session
            pass
        async with AsyncExitStack() as stack:
            tracing = stack.enter_context(open_trace(current))  # closed last: sends what is left
            checkpointer = await stack.enter_async_context(open_saver(current))
            app.state.documents = stack.enter_context(open_documents(current))
            app.state.uploads = await stack.enter_async_context(open_uploads(current))
            app.state.settings = current
            app.state.runs = AgentRuns(current, model, reviewer, checkpointer, open_tools, tracing)
            yield

    app = FastAPI(
        title="avtalsagent",
        summary="Agenten som besvarar frågor om ramavtalen, med kontrollerade citat.",
        lifespan=lifespan,
    )
    app.include_router(agui_router)
    app.include_router(documents_router)
    app.include_router(uploads_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """The process serves HTTP; the database and avtal-mcp are not asked."""
        return {"status": "ok"}

    return app
