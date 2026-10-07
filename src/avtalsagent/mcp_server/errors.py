"""The errors a tool raises on purpose, with a message written for the model.

What:
    `NotFoundError`: what the arguments point to does not exist or is held
    back. `AmbiguousError`: they point to more than one thing.
    `MissingArgumentError`: none of the arguments a tool needs one of is set.
    `UnavailableError`: the tool cannot answer now (no embedding model, no
    search index, or the model did not answer). `ArgumentError`: arguments
    that are each valid but cannot be combined.

Why:
    The MCP SDK turns a `ToolError` into an error result the model reads, and
    the run goes on: the model can correct its call. Its message is the only
    help the model gets, so it says in Swedish what went wrong and what to do
    next ("Kontrollera numret med get_outline"). Any other exception is
    replaced by a fixed message in `server.py`, so SQL and connection details
    never reach the model.

How:
    Subclasses of FastMCP's `ToolError`, so `server.py` lets them through.
    A search that finds nothing is not an error: it returns an empty list.
"""

from mcp.server.fastmcp.exceptions import ToolError


class NotFoundError(ToolError):
    """The file, section or agreement does not exist, or the ingestion holds it back."""


class AmbiguousError(ToolError):
    """The arguments match more than one section; the message lists them."""


class MissingArgumentError(ToolError):
    """A tool that needs at least one of several arguments got none of them."""


class UnavailableError(ToolError):
    """The tool cannot answer now: what it needs is missing or did not answer."""


class ArgumentError(ToolError):
    """The arguments are each valid but cannot be combined; the message says why."""
