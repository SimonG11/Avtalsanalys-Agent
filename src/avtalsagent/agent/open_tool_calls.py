"""A result for every tool call the model reads, also for a call whose run broke off.

What:
    `AnswerOpenToolCalls`, middleware that shows the model a tool result
    for every tool call in the history: a call without one gets the error
    `CALL_BROKEN_OFF` right after it. `answer_open_calls(messages)` is the
    rule itself, without the middleware.

Why:
    A run can end between the model's tool call and the tool's result:
    the session to avtal-mcp breaks during the call, a tool raises
    something other than a `ToolException`, or the user leaves the page
    and the run is cancelled. The checkpoint then holds the call without a
    result, and OpenAI refuses every later request in that conversation
    ("No tool output found for function call"), so one broken run would
    break the conversation for good. The API keeps conversations for as
    long as the database does (ADR 0014).

How:
    Before each model call the history is copied with an error result
    inserted after each call that has none, and the model gets the copy.
    The checkpoint keeps the history as it was. A waiting `ask_user` is not
    touched: while it waits the graph does not run (the API sends the
    question again), and when the user answers, the tool gives its result
    before the model is called.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

CALL_BROKEN_OFF = "Verktygsanropet avbröts innan det gav något svar. Gör om det om det behövs."


class AnswerOpenToolCalls(AgentMiddleware[Any, None]):
    """Gives the model an error result for each tool call in the history that has none."""

    def wrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(request.override(messages=answer_open_calls(request.messages)))

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(request.override(messages=answer_open_calls(request.messages)))


def answer_open_calls(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """The messages, with an error result right after each tool call that has no result."""
    answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    complete: list[AnyMessage] = []
    for message in messages:
        complete.append(message)
        if isinstance(message, AIMessage):
            complete.extend(
                ToolMessage(
                    content=CALL_BROKEN_OFF,
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
                for call in message.tool_calls
                if call["id"] is not None and call["id"] not in answered
            )
    return complete
