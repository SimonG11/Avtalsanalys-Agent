"""The agent that answers questions about the framework agreements (M7).

What:
    One LangGraph graph, built with LangChain's `create_agent` (`graph.py`):
    the model (`model.py`) works with avtal-mcp's tools (`mcp_tools.py`) and
    `ask_user` (`ask_user.py`), and hands in its answer as a `FinalAnswer`
    (`schemas.py`). Before the answer reaches the user, `CitationCheck`
    (`middleware.py`) checks every quote against the section it cites, read
    again through the MCP session (`sections.py`), and turns the draft into
    the web app's `answer`: verified, with reservation, or no answer.
    `prompts.py` holds the system prompt, `checkpointer.py` where a
    conversation's checkpoints are kept, and `__main__.py` the command line.

Why:
    The steps around the agent's loop (reset, check, answer) are middleware
    hooks in one graph rather than nodes of an outer graph (ADR 0013,
    amending ADR 0002): the web app then sees the agent's steps while a run
    waits for the user. The check is plain code the model cannot get past;
    it trusts neither the model nor the message history, which a client
    sends and could change.

How:
    `build_agent(model, tools, reader, checkpointer, settings)` returns the
    compiled graph. The graph runs async only (`ainvoke`, `astream`), since
    the check reads sections through the async MCP client. The validation
    rules themselves are pure functions in `avtalsagent.validation`.
"""
