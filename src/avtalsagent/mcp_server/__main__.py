"""Command line of avtal-mcp: `python -m avtalsagent.mcp_server {stdio,http}`.

What:
    `stdio` serves the tools over stdin and stdout; `http` serves them as
    streamable HTTP at `/mcp` (`--host`, default 127.0.0.1; `--port`,
    default MCP_PORT, 8001), with a health check at `/health`.

Why:
    stdio is what MCP Inspector and desktop clients start a server with;
    HTTP is how the agent reaches the server in its own container. In a
    container the server is started with `uvicorn --factory
    avtalsagent.mcp_server.server:create_app` instead, which serves the
    same app.

How:
    Runs `server.main`. Both transports read DATABASE_URL and, for
    `search_documents`, OPENAI_API_KEY from the environment or `.env`.
"""

from avtalsagent.mcp_server.server import main

if __name__ == "__main__":
    main()
