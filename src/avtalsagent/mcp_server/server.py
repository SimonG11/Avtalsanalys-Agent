"""The avtal-mcp server: registers the tools and serves them over HTTP or stdio.

What:
    `build_server` makes a FastMCP server (`AvtalMCP`) of the tools in
    `tools.TOOLS`: each read-only, with a closed JSON schema of its
    arguments and a schema of its result, and a `/health` route for the
    container's health check. `create_app` is
    the HTTP app of the configured server (`uvicorn --factory
    avtalsagent.mcp_server.server:create_app`), and `main` the command line
    (`python -m avtalsagent.mcp_server {stdio,http}`).

Why:
    A tool is a plain function of a database session and typed arguments,
    so it can be tested without MCP and reviewed on its own. The server adds
    what every tool needs the same way: a session per call, the embedding
    model for the search, a worker thread for the synchronous query (the
    MCP SDK 1.x would otherwise block its event loop), and error messages
    without internals. The SDK sends the text of any exception to the
    model, which for a database error can be SQL or a connection string;
    here only a tool's own `ToolError` (`errors.py`) gets through, and
    anything else becomes a fixed Swedish message, with the details logged
    for us. The server keeps no session state between calls
    (`stateless_http`), so a restart is invisible to the agent and several
    workers need no sticky sessions; it answers in plain JSON, since no tool
    streams. It accepts only the Host headers in MCP_ALLOWED_HOSTS
    (protection against DNS rebinding), which must include the name other
    containers reach it by.

How:
    `_with_session` wraps each tool in an async function whose signature
    leaves out `session` (and `embedder`, when it is the second parameter),
    so FastMCP builds the argument schema from the rest and validates every
    call against it before the tool runs: a malformed hash or a limit of
    500 never opens a session. The SDK drops an argument whose name the
    schema does not have, so a misspelt filter would give an unfiltered
    answer; `AvtalMCP`, a small FastMCP subclass, publishes each schema with
    `additionalProperties: false` and refuses such a call, before any
    session, with a Swedish `ToolError` that names the tool's arguments.
    The wrapper opens a session from `sessions`, calls the tool with it in a
    worker thread and closes it, which ends the read-only transaction. The
    tool's docstring is its description, and its
    return model the output schema. `create_app` and `main` read through a
    read-only engine (`create_db_engine(read_only=True)`) and embed questions
    with the configured OpenAI model, or with none when OPENAI_API_KEY is not
    set: then `search_documents` answers with an error and the other tools
    work. The key is never logged. A question is embedded with a short
    timeout and one retry (`QUERY_TIMEOUT`, `QUERY_RETRIES`), since the call
    holds a database connection and a worker thread: a hung API becomes the
    search's error in seconds, not after the client's 600 s three times.
"""

import argparse
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import anyio.to_thread
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ContentBlock, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from avtalsagent.config import get_settings
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.mcp_server.tools import TOOLS
from avtalsagent.retrieval.embedder import Embedder, openai_embedder

_log = logging.getLogger(__name__)

# Every tool only reads, gives the same answer to the same call and works on a closed set
# of data: the register and the documents (the search's embedding model only turns the
# question into a vector).
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

INSTRUCTIONS = """\
avtal-mcp läser Statens inköpscentrals ramavtal: registret över giltiga avtal \
(avtalsnummer, leverantörer, ramavtalsområden, giltighetstider) och avtalsdokumentens text, \
uppdelad i avsnitt. Verktygen läser bara och ändrar ingenting. Avsnitt som inläsningen \
håller tillbaka visas inte.
Börja med search_register för frågor om vilka avtal och leverantörer som finns och när de \
gäller, eller med search_documents för vad avtalen säger. Läs sedan hela avsnittet med \
read_section innan du citerar det, se ett dokuments innehåll med get_outline, följ en \
hänvisning med resolve_reference och se ett avtals eller områdes dokument med list_documents.
Citera med fälten sha256, file_title, section_number, section_title och page_start från \
verktygens svar."""

# The only texts the model gets for an error that is not a tool's own `ToolError`.
DATABASE_ERROR = (
    "Databasen kunde inte svara just nu. Försök igen om en stund; går det inte, "
    "säg att uppgiften inte kunde hämtas."
)
INTERNAL_ERROR = (
    "Ett internt fel uppstod i verktyget. Försök en gång till; går det inte, "
    "säg att uppgiften inte kunde hämtas."
)

# Embedding a question is one small request, made while the search holds a database
# connection and a worker thread; the OpenAI client would wait 600 s and retry twice.
QUERY_TIMEOUT = 15.0  # seconds per request
QUERY_RETRIES = 1

ToolFunction = Callable[..., BaseModel]


class AvtalMCP(FastMCP):
    """FastMCP whose tools take only the arguments in their schema.

    The SDK 1.x ignores an argument it does not know (its argument models
    allow extra keys, and FastMCP turns the low-level server's schema check
    off), so a misspelt filter would be dropped and the answer unfiltered.
    """

    async def list_tools(self) -> list[MCPTool]:
        """The tools, each argument schema closed (`additionalProperties: false`)."""
        return [
            tool.model_copy(
                update={"inputSchema": {**tool.inputSchema, "additionalProperties": False}}
            )
            for tool in await super().list_tools()
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Refuse an argument the tool does not take; otherwise call it as FastMCP does."""
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is not None:  # an unknown tool is FastMCP's error
            known = list(tool.inputSchema.get("properties", {}))
            if unknown := sorted(set(arguments) - set(known)):
                taken = f"tar bara {', '.join(known)}" if known else "tar inga argument"
                # A ToolError's text is the error result the model reads (the low-level server).
                raise ToolError(
                    f"{name} har inget argument som heter {', '.join(unknown)}. Verktyget "
                    f"{taken}; rätta namnet och anropa igen."
                )
        return await super().call_tool(name, arguments)


def _with_session(
    fn: ToolFunction, sessions: Callable[[], Session], embedder: Embedder | None
) -> Callable[..., Awaitable[BaseModel]]:
    """`fn(session, [embedder,] **arguments)` as the async MCP tool `tool(**arguments)`."""
    signature = inspect.signature(fn, eval_str=True)
    params = list(signature.parameters.values())
    if not params or params[0].name != "session":
        raise TypeError(f"{fn.__name__}: a tool's first parameter must be `session`")
    wants_embedder = len(params) > 1 and params[1].name == "embedder"
    injected = 2 if wants_embedder else 1

    def run(arguments: dict[str, Any]) -> BaseModel:
        with sessions() as session:
            if wants_embedder:
                return fn(session, embedder, **arguments)
            return fn(session, **arguments)

    @functools.wraps(fn)
    async def tool(**arguments: Any) -> BaseModel:
        try:
            return await anyio.to_thread.run_sync(run, arguments)
        except ToolError:
            raise  # written for the model (errors.py)
        except SQLAlchemyError:
            _log.exception("Database error in the tool %s", fn.__name__)
            raise ToolError(DATABASE_ERROR) from None
        except Exception:
            _log.exception("Unexpected error in the tool %s", fn.__name__)
            raise ToolError(INTERNAL_ERROR) from None

    # FastMCP builds the argument schema from inspect.signature(), which reads __signature__.
    tool.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=params[injected:]
    )
    return tool


async def _health(request: Request) -> Response:
    """The container's health check: the process answers HTTP (not the database)."""
    return JSONResponse({"status": "ok"})


def build_server(
    sessions: Callable[[], Session],
    embedder: Embedder | None,
    *,
    allowed_hosts: Sequence[str],
    tools: Sequence[ToolFunction] = TOOLS,
) -> FastMCP:
    """The avtal-mcp server of `tools`, each called with a session from `sessions`.

    `sessions` is a `sessionmaker`, or anything that makes a `Session`;
    `embedder` is passed to the tools that ask for it (None without an
    OpenAI key). `allowed_hosts` are the Host headers HTTP requests may
    carry, e.g. "localhost:*" or "mcp:8001".
    """
    server = AvtalMCP(
        "avtal-mcp",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        log_level="WARNING",  # INFO logs a line for every request
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            # A browser on this machine, such as MCP Inspector, sends an Origin header.
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )
    for fn in tools:
        if not fn.__doc__:
            raise TypeError(f"{fn.__name__}: a tool needs a docstring, the model's description")
        server.add_tool(
            _with_session(fn, sessions, embedder),
            # FastMCP passes the docstring on with its indentation.
            description=inspect.cleandoc(fn.__doc__),
            annotations=READ_ONLY,
            structured_output=True,
        )
    # Before streamable_http_app(), which copies the custom routes.
    server.custom_route("/health", methods=["GET"])(_health)
    return server


def _server_from_settings() -> FastMCP:
    """The server on a read-only engine, with the configured embedder."""
    settings = get_settings()
    embedder = openai_embedder(settings, timeout=QUERY_TIMEOUT, max_retries=QUERY_RETRIES)
    server = build_server(
        session_factory(create_db_engine(read_only=True)),
        embedder,
        allowed_hosts=settings.mcp_allowed_hosts,
    )
    if embedder is None:
        _log.warning("OPENAI_API_KEY is not set: search_documents answers with an error")
    return server


def create_app() -> Starlette:
    """The HTTP app: `uvicorn --factory avtalsagent.mcp_server.server:create_app`.

    MCP is at `/mcp`, the health check at `/health`.
    """
    return _server_from_settings().streamable_http_app()


def build_parser() -> argparse.ArgumentParser:
    """The transports and their options."""
    parser = argparse.ArgumentParser(
        prog="python -m avtalsagent.mcp_server",
        description="avtal-mcp: the agent's read-only tools over the register and the documents.",
    )
    transports = parser.add_subparsers(dest="transport", required=True)
    transports.add_parser("stdio", help="over stdin and stdout, e.g. for MCP Inspector")
    http = transports.add_parser("http", help="streamable HTTP at /mcp")
    http.add_argument(
        "--host",
        default="127.0.0.1",
        help="the address to listen on (default 127.0.0.1; 0.0.0.0 in a container)",
    )
    http.add_argument("--port", type=tcp_port, help="the port (default MCP_PORT, 8001)")
    return parser


def tcp_port(value: str) -> int:
    """An argparse type: a TCP port, 1 to 65535."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if not 0 < number < 65536:
        raise argparse.ArgumentTypeError(f"must be a port from 1 to 65535, not {value!r}")
    return number


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.transport == "stdio":
        _server_from_settings().run("stdio")
    else:
        uvicorn.run(create_app(), host=args.host, port=args.port or get_settings().mcp_port)
