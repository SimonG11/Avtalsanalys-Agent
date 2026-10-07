"""One gold question through the agent, and the tokens and cost of its model calls (M11).

What:
    `run_question(graph, question, ...)` asks the agent's graph one question
    in a thread of its own and returns a `QuestionRun`: the checked answer,
    the draft it was checked from, the tools called, the agent's questions
    to the user, the drafts the check sent back or the schema refused, the
    time, the tokens of every model call by model, and the error, if any.
    `UsageCounter` is the callback that counts the tokens, `TokenUse` one
    model's count, and `cost` its price in dollars at PRICES.

Why:
    The measurement must see what a user gets and what it costs, from the
    graph the API runs: the answer in the state, and the run's messages for
    how it got there. A question that fails or hangs must not stop the
    others, so its error is recorded instead.

How:
    The graph runs with `ainvoke` until it stops without an interrupt; a
    stop at `ask_user` is resumed with ASK_USER_REPLY, since the gold
    questions are meant to be answered as asked. `anyio.fail_after` bounds
    the whole question. The final state is read even after an error: the
    answer is None unless the check set one. The draft is the last
    `FinalAnswer` call that parses (`last_draft`). The callback runs inline
    and reads each call's model from LangChain's `ls_model_name` metadata
    and its tokens from the message's `usage_metadata`; cached input and
    reasoning are parts of the input and output tokens, as OpenAI reports
    them. The price table has no price for cached input, so `cost` takes
    the share of the input price that a cached token costs: 1 gives an
    upper bound, 0 a lower one. An error is kept as its type and message,
    with the key and the database password redacted.
"""

import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anyio
from langchain.agents.middleware import InputAgentState
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Interrupt
from pydantic import ValidationError

from avtalsagent.agent.ask_user import ask_user
from avtalsagent.agent.graph import ANSWER_SUBMITTED, AvtalAgent
from avtalsagent.agent.middleware import FINAL_ANSWER_TOOL
from avtalsagent.agent.schemas import Answer, FinalAnswer

# What the measurement answers when the agent asks the user (ask_user).
ASK_USER_REPLY = "Jag har inget att tillägga: svara utifrån frågan som den är ställd."
# How long an error may be in the report.
ERROR_CHARS = 300


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens."""

    input: float
    output: float


# From the model table of the architecture validation (03-arkitekturvalidering.md, October
# 2026), which has no price for cached input.
PRICES: Mapping[str, Price] = {
    "gpt-6.1-sol": Price(input=2.0, output=10.0),
    "gpt-6-astra": Price(input=10.0, output=50.0),
    "gpt-6-luna": Price(input=0.1, output=0.5),
}


# --- Tokens -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUse:
    """One model's calls and their tokens; cached and reasoning tokens are parts of the rest."""

    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0  # of input_tokens
    output_tokens: int = 0
    reasoning_tokens: int = 0  # of output_tokens

    def __add__(self, other: "TokenUse") -> "TokenUse":
        return TokenUse(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class UsageCounter(BaseCallbackHandler):
    """Counts every chat model call made under it, and its tokens, by model name."""

    run_inline = True  # on the event loop, in order: no lock is needed

    def __init__(self) -> None:
        self.by_model: dict[str, TokenUse] = {}
        self._models: dict[uuid.UUID, str] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: uuid.UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = (metadata or {}).get("ls_model_name") or (kwargs.get("invocation_params") or {}).get(
            "model"
        )
        self._models[run_id] = str(name or "okänd modell")

    def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._add(run_id, token_use(response))

    def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._add(run_id, TokenUse(calls=1))

    def _add(self, run_id: uuid.UUID, use: TokenUse) -> None:
        model = self._models.pop(run_id, "okänd modell")
        self.by_model[model] = self.by_model.get(model, TokenUse()) + use


def token_use(response: LLMResult) -> TokenUse:
    """One model call's tokens, from the usage the model reported for its message."""
    use = TokenUse(calls=1)
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if not usage:
                continue
            cached = (usage.get("input_token_details") or {}).get("cache_read") or 0
            reasoning = (usage.get("output_token_details") or {}).get("reasoning") or 0
            use = use + TokenUse(
                input_tokens=usage.get("input_tokens", 0),
                cached_input_tokens=cached,
                output_tokens=usage.get("output_tokens", 0),
                reasoning_tokens=reasoning,
            )
    return use


def total_use(uses: Iterable[Mapping[str, TokenUse]]) -> dict[str, TokenUse]:
    """The token use of several runs, by model."""
    total: dict[str, TokenUse] = {}
    for use in uses:
        for model, tokens in use.items():
            total[model] = total.get(model, TokenUse()) + tokens
    return dict(sorted(total.items()))


def cost(
    use: Mapping[str, TokenUse], *, cached: float, prices: Mapping[str, Price] = PRICES
) -> float | None:
    """Dollars at `prices`, a cached input token at `cached` times the input price.

    None when a model has no price.
    """
    dollars = 0.0
    for model, tokens in use.items():
        price = prices.get(model)
        if price is None:
            return None
        uncached = tokens.input_tokens - tokens.cached_input_tokens
        dollars += (
            uncached * price.input
            + tokens.cached_input_tokens * price.input * cached
            + tokens.output_tokens * price.output
        ) / 1e6
    return dollars


def cost_range(use: Mapping[str, TokenUse]) -> tuple[float, float] | None:
    """The cost with cached input at no cost and at the full input price."""
    low, high = cost(use, cached=0.0), cost(use, cached=1.0)
    return (low, high) if low is not None and high is not None else None


# --- One question through the agent -----------------------------------------------------------


@dataclass(frozen=True)
class QuestionRun:
    """What one question's run produced: the answer, its draft and how the agent got there."""

    answer: Answer | None  # None when the run failed before the check set one
    draft: FinalAnswer | None  # the draft the answer was checked from
    tools: tuple[str, ...]  # the avtal-mcp tools called, in order
    tool_errors: int  # tool results with status error
    asked: tuple[str, ...]  # the agent's questions to the user
    check_retries: int  # drafts the answer check sent back
    refused_drafts: int  # drafts whose form the schema refused
    seconds: float
    usage: Mapping[str, TokenUse]  # by model: the agent's and the reviewer's calls
    error: str | None = None


async def run_question(
    graph: AvtalAgent,
    question: str,
    *,
    thread_id: str,
    timeout: float,
    redact: Callable[[str], str],
) -> QuestionRun:
    """Ask `graph` the question and read what it did; an error or a timeout is recorded."""
    usage = UsageCounter()
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}, "callbacks": [usage]}
    run_input: InputAgentState | Command[Any] = {
        "messages": [{"role": "user", "content": question}]
    }
    asked: list[str] = []
    error: str | None = None
    started = time.monotonic()
    try:
        with anyio.fail_after(timeout):
            while True:
                await graph.ainvoke(run_input, config)
                state = await graph.aget_state(config)
                if not state.interrupts:
                    break
                asked += [_asked(interrupt.value) for interrupt in state.interrupts]
                run_input = Command(resume=_replies(state.interrupts))
    except TimeoutError:
        error = f"tidsgränsen på {timeout:g} s nåddes"
    except Exception as failure:  # recorded; the next question is asked all the same
        error = redact(error_text(failure))
    seconds = time.monotonic() - started
    values: Mapping[str, Any] = {}
    try:
        values = (await graph.aget_state(config)).values
    except Exception as failure:  # the checkpointer is in memory; this is not expected
        error = error or redact(error_text(failure))
    return read_run(values, asked, seconds, usage.by_model, error)


def read_run(
    values: Mapping[str, Any],
    asked: Sequence[str],
    seconds: float,
    usage: Mapping[str, TokenUse],
    error: str | None,
) -> QuestionRun:
    """The run as the graph's final state shows it."""
    messages: list[BaseMessage] = list(values.get("messages") or [])
    raw = values.get("answer")
    answer = Answer.model_validate(raw) if raw is not None else None
    tools = tuple(
        call["name"]
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] not in (FINAL_ANSWER_TOOL, ask_user.name)
    )
    results = [message for message in messages if isinstance(message, ToolMessage)]
    drafts = [result for result in results if result.name == FINAL_ANSWER_TOOL]
    return QuestionRun(
        answer=answer,
        draft=last_draft(messages),
        tools=tools,
        tool_errors=sum(1 for r in results if r.status == "error" and r.name != FINAL_ANSWER_TOOL),
        asked=tuple(asked),
        check_retries=sum(1 for draft in drafts if draft.status == "error"),
        refused_drafts=sum(
            1 for draft in drafts if draft.status != "error" and draft.text != ANSWER_SUBMITTED
        ),
        seconds=seconds,
        usage=dict(usage),
        error=error[:ERROR_CHARS] if error else None,
    )


def last_draft(messages: Sequence[BaseMessage]) -> FinalAnswer | None:
    """The last `FinalAnswer` call that parses: the draft the answer was checked from.

    A draft the check sends back stays the answer when no later draft
    parses (the fallback answer), and a later one that parses replaces it.
    """
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in reversed(message.tool_calls):
            if call["name"] != FINAL_ANSWER_TOOL:
                continue
            try:
                return FinalAnswer.model_validate(call["args"])
            except ValidationError:
                continue
    return None


def error_text(error: BaseException) -> str:
    """An error and the errors in its group as text: `ConnectError: All connection attempts…`."""
    return "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in _leaves(error))


def _leaves(error: BaseException) -> Iterator[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
    else:
        yield error


def _asked(payload: Any) -> str:
    fields = payload if isinstance(payload, Mapping) else {"question": payload}
    return " ".join(str(fields.get("question")).split())


def _replies(interrupts: Sequence[Interrupt]) -> Any:
    """ASK_USER_REPLY for each question, as `Command(resume=...)` takes them."""
    replies = {interrupt.id: ASK_USER_REPLY for interrupt in interrupts}
    return ASK_USER_REPLY if len(replies) == 1 else replies
