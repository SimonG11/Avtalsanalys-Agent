"""The agent's stated purpose: a `syfte` argument on each avtal-mcp tool the model is offered.

What:
    `StatedPurpose(tools)`, a `create_agent` middleware. The model sees each
    of `tools` with one more required argument, `syfte`: a short Swedish
    sentence on what the agent wants to find out with the call, and why.
    Before the tool runs, the middleware checks `syfte` and removes it, so
    the tool gets exactly the arguments it would get without it.
    `with_purpose(tool)` is the tool as the model sees it, and
    `purpose_problem(args)` what is wrong with a call's `syfte`, if anything.

Why:
    Simon wants to see how the agent thinks (ADR 0025). OpenAI's reasoning
    summaries came in 1 of 47 model calls at the effort `low`, in English,
    and were sometimes wrong. A sentence the agent writes in each call comes
    with every step, in Swedish, for a few output tokens. It is the agent's
    stated reason, not its hidden reasoning, and nothing checks it; the
    answer is checked as before. The argument lives in the graph, not in
    avtal-mcp: the server's tools stay the same for every client, and the
    fixed-workflow baseline calls `McpTools.tools` directly. `ask_user` and
    `FinalAnswer` get no `syfte`: the question is shown as it is, and the
    answer is not a step.

How:
    `wrap_model_call` swaps each of the tools in the model request for a
    copy (`with_purpose`) whose argument schema has `syfte` first, so the
    model writes it first and the web app can show it before the other
    arguments, and in `required`, with a description and at most
    `PURPOSE_MAX_CHARS` characters. The copy is only bound to the model:
    `ToolNode` runs the original by its name, with the adapter's error
    handling (`handle_tool_errors`), and `ToolErrorMiddleware` as before.
    OpenAI does not hold the model to the schema (the tools are not
    strict), so `wrap_tool_call` checks the call: without `syfte`, with an
    empty one or a longer one, the call gets a tool result with status
    error, in Swedish, without running the tool, and the model can call
    again; the run goes on. A call that passes goes on without `syfte`
    (`ToolCallRequest.override`). The model's message keeps `syfte` in its
    call: the web app reads it from the streamed `TOOL_CALL_ARGS` and from
    `MESSAGES_SNAPSHOT`, the command line from the step.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.pydantic import model_json_schema
from langgraph.types import Command

PURPOSE = "syfte"
PURPOSE_MAX_CHARS = 200
PURPOSE_DESCRIPTION = (
    "En kort mening om vad du vill ta reda på med anropet och varför. Användaren ser den som "
    "din tanke."
)
PURPOSE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": PURPOSE_DESCRIPTION,
    "minLength": 1,
    "maxLength": PURPOSE_MAX_CHARS,
}


class StatedPurpose(AgentMiddleware[Any, None]):
    """Offers `tools` with a required `syfte`, and runs them without it."""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        super().__init__()
        self._offered = {tool.name: with_purpose(tool) for tool in tools}

    def wrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._offer(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._offer(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        checked = self._check(request)
        return checked if isinstance(checked, ToolMessage) else handler(checked)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        checked = self._check(request)
        return checked if isinstance(checked, ToolMessage) else await handler(checked)

    def _offer(self, request: ModelRequest[None]) -> ModelRequest[None]:
        """The request with each of the tools as the model sees it, with `syfte`."""
        tools = [
            self._offered.get(tool.name, tool) if isinstance(tool, BaseTool) else tool
            for tool in request.tools
        ]
        return request.override(tools=tools)

    def _check(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        """The call without `syfte`, or the refusal the model reads when `syfte` is wrong."""
        call = request.tool_call
        if call["name"] not in self._offered:
            return request
        args = call["args"]
        problem = purpose_problem(args)
        if problem is not None:
            return ToolMessage(
                content=(
                    f"Anropet nekades: {problem}. Ange i {PURPOSE} en kort mening, högst "
                    f"{PURPOSE_MAX_CHARS} tecken, om vad du vill ta reda på med anropet och "
                    f"varför, och anropa {call['name']} igen."
                ),
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        stripped = {key: value for key, value in args.items() if key != PURPOSE}
        return request.override(tool_call={**call, "args": stripped})


def with_purpose(tool: BaseTool) -> BaseTool:
    """A copy of `tool` whose argument schema has `syfte` first and required, for the model.

    The copy is for binding to the model only; it is never run.
    """
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else None
    if schema is None:
        call_schema = tool.tool_call_schema
        schema = call_schema if isinstance(call_schema, dict) else model_json_schema(call_schema)
    properties: dict[str, Any] = dict(schema.get("properties", {}))
    if PURPOSE in properties:
        raise ValueError(f"the tool {tool.name} already has an argument {PURPOSE!r}")
    offered = {
        **schema,
        "properties": {PURPOSE: PURPOSE_SCHEMA, **properties},
        "required": [PURPOSE, *schema.get("required", [])],
    }
    return tool.model_copy(update={"args_schema": offered})


def purpose_problem(args: Mapping[str, Any]) -> str | None:
    """What is wrong with the call's `syfte`, in Swedish for the model; None when nothing is."""
    if PURPOSE not in args:
        return f"{PURPOSE} saknas"
    value = args[PURPOSE]
    if not isinstance(value, str):
        return f"{PURPOSE} ska vara text"
    if not value.strip():
        return f"{PURPOSE} är tomt"
    if len(value.strip()) > PURPOSE_MAX_CHARS:
        return f"{PURPOSE} har {len(value.strip())} tecken"
    return None
