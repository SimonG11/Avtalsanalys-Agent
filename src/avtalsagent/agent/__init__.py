"""The agent that answers questions about the framework agreements (M7, M8).

What:
    One LangGraph graph, built with LangChain's `create_agent` (`graph.py`):
    the model (`model.py`) works with avtal-mcp's tools (`mcp_tools.py`) and
    `ask_user` (`ask_user.py`), and hands in its answer as a `FinalAnswer`
    (`schemas.py`). Before the answer reaches the user, `AnswerCheck`
    (`middleware.py`) checks every quote against the section it cites and
    every register fact against the register, both read again through the
    MCP session (`sections.py`, `register_reader.py`), has a second model
    review the answer (`reviewer.py`), sends a failed draft back to the
    model at most twice, and turns the draft into the web app's `answer`:
    verified, with reservation, or no answer.
    `prompts.py` holds the system prompt, `checkpointer.py` where a
    conversation's checkpoints are kept, and `__main__.py` the command line.
    The user's own files, uploaded in the conversation, are read with
    `list_uploads` and `read_upload` and named in the prompt
    (`upload_prompt.py`); a citation of one is checked against the stored
    text (`upload_readers.py`, ADR 0026).

Why:
    The steps around the agent's loop (reset, check, answer) are middleware
    hooks in one graph rather than nodes of an outer graph (ADR 0013,
    amending ADR 0002): the web app then sees the agent's steps while a run
    waits for the user. The check is plain code the model cannot get past;
    it trusts neither the model nor the message history, which a client
    sends and could change.

How:
    `build_agent(model, mcp, reviewer, checkpointer, settings)` returns the
    compiled graph. The graph runs async only (`ainvoke`, `astream`), since
    the check reads through the async MCP client. The validation
    rules themselves are pure functions in `avtalsagent.validation`.
"""
