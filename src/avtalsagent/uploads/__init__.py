"""The user's own files: reading an uploaded file and keeping it for its conversation.

What:
    `parse.py` reads an uploaded file into sections (`file_type.py` decides
    what the file is, `extract.py` takes out its text, `sections.py` cuts it
    at its headings), and `parse_process.py` runs it in a child process;
    `store.py` keeps the files per conversation, in memory or in Postgres
    (`postgres_store.py`), and `open_store.py` opens the one UPLOAD_STORE
    names. `api/uploads.py` has the routes.

Why:
    The user can upload a file in the chat and ask the agent to compare it
    with the framework agreements (webbapp-kontrakt.md, points 33-38). The
    file belongs to its conversation alone, and avtal-mcp, which is
    read-only over the shared corpus, never sees it. So the API keeps the
    files itself and the agent reads them through the store, not through
    avtal-mcp.

How:
    Parsing is pure and synchronous (the API and the command line run it
    in a child process with a time and a memory limit); the store is
    asynchronous, as the agent's other readers are.
"""
