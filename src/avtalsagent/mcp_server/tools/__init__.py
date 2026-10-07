"""The tools of avtal-mcp, one function per module, in the order of the web app's contract.

What:
    `TOOLS`: `search_documents`, `read_section`, `get_outline`,
    `resolve_reference`, `list_documents`, `search_register`,
    `find_amendments` and `calculate_date`.

Why:
    `server.py` registers the tools in this order, and the agent lists them
    in it: the search first, then reading, then the register, the
    amendments and the dates. One module per tool keeps each one small
    enough to review and test on its own.

How:
    Each tool is a plain function `tool(session, [embedder,] **arguments)`
    that returns a Pydantic model (`calculate_date`, which reads no
    database, takes no session); its docstring, in Swedish, is the
    description the model reads. The modules are imported as modules, so
    `tools.search_documents` stays the module and a test can patch what it
    uses.
"""

from collections.abc import Callable

from pydantic import BaseModel

from avtalsagent.mcp_server.tools import (
    calculate_date,
    find_amendments,
    get_outline,
    list_documents,
    read_section,
    resolve_reference,
    search_documents,
    search_register,
)

TOOLS: list[Callable[..., BaseModel]] = [
    search_documents.search_documents,
    read_section.read_section,
    get_outline.get_outline,
    resolve_reference.resolve_reference,
    list_documents.list_documents,
    search_register.search_register,
    find_amendments.find_amendments,
    calculate_date.calculate_date,
]
