"""The API (M9): the agent for the web app, and the documents its answers cite.

What:
    A FastAPI app (`app.py`) with the agent over AG-UI at `POST /agui`
    (`agui.py`), the cited documents' PDFs at
    `GET /api/documents/{sha256}/pdf` (`documents.py`) and a health check at
    `GET /health`. `python -m avtalsagent.api` serves it (`__main__.py`).

Why:
    The web app (M10, ADR 0010) talks to the agent through AG-UI, the
    protocol CopilotKit speaks, and shows a cited page next to the answer
    (webbapp-kontrakt.md). The API is a thin layer over what M6 and M7
    built: the agent and its graph (`avtalsagent.agent`), avtal-mcp for the
    data and Postgres for the conversations' checkpoints. It adds no
    business logic of its own (ADR 0014).

How:
    `create_app()` builds the app. Its lifespan makes the model clients
    (the agent's and the reviewer's), checks once that avtal-mcp answers,
    and opens the checkpointer and a
    read-only database engine until the app stops. Each request to `/agui`
    opens its own session to avtal-mcp (`agui.AgentRuns`) and runs the
    agent on its own copy of ag-ui-langgraph's `LangGraphAgent`.
"""
