"""avtal-mcp: the agent's read-only tools over the register and the documents (M6).

What:
    An MCP server with eight tools: `search_documents` (the hybrid search),
    `read_section`, `get_outline`, `resolve_reference`, `list_documents`,
    `search_register`, `find_amendments` and `calculate_date`. Each tool is a plain function in
    `tools/` that takes typed arguments, and a database session when it
    reads the database (`calculate_date` does not), and returns a Pydantic
    model.

Why:
    The agent gets its data only through these tools (ADR 0003): it cannot
    write, cannot send SQL and sees nothing the ingestion holds back. Each
    tool can be tested without a language model, and other MCP clients (MCP
    Inspector, Claude Desktop) can use the same tools.

How:
    `server.py` registers the functions with the official MCP SDK's FastMCP,
    hides the session from each tool's schema, refuses argument names a
    schema does not have and turns unexpected errors into a message without
    internals. `arguments.py` holds the argument types
    with their Swedish descriptions, which the model reads; `results.py` the
    fields every section result shares, so the answer's citations can copy
    them; `errors.py` the errors written for the model; `visibility.py` the
    rule of what a tool may show (the same as the search index's). The
    server reads through a read-only engine (`create_db_engine(read_only=True)`).
    It runs over streamable HTTP (`python -m avtalsagent.mcp_server http`) or
    stdio (`python -m avtalsagent.mcp_server stdio`).
"""
