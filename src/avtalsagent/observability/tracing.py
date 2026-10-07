"""Tracing of the agent's runs in Langfuse (ADR 0021).

What:
    `open_tracing(settings)` starts Langfuse's client when LANGFUSE_PUBLIC_KEY
    and LANGFUSE_SECRET_KEY are set, and yields a `Tracing`; without them
    tracing is off. `Tracing.run_config(...)` is the part of a question's
    LangChain config that traces it: Langfuse's callback handler with the
    question's own trace id, and the trace's name, session (the
    conversation's thread), tags and metadata. When tracing is off it is
    empty, and `traced(config, trace)` then returns the config itself, so a
    run is the same with or without tracing. Leaving the block sends what
    is left.

Why:
    The command line shows a run's steps, but not what each model call cost
    or how long it took, and the API shows nothing after the fact. A trace
    holds the whole run: the agent's model calls with their tokens and
    times, every tool call with its arguments and result, the answer
    check, the reviewer's verdict and the new attempts, grouped by
    conversation. That shows how the agent worked on a question and is what
    the architecture plan (section 8) asks for. Langfuse Cloud's EU region
    is used, as decided in the validation of the architecture (point 9), so
    no ClickHouse has to run beside the app; the agreements are public. The
    keys are optional so the demo, the tests and CI run without an account.

How:
    The client is made with the settings' keys and address, not from the
    environment, since Pydantic reads `.env` into the settings only. The
    handler finds the client by its public key; Langfuse keeps one client
    per public key in the process, so each process opens the tracing once
    (the API's lifespan, the command line, the measurement). Each question
    gets a handler of its own, which keeps concurrent questions apart, and a
    trace id of its own: a run that stops at `ask_user` and is resumed is
    two runs of the graph, and the fixed id puts both in the question's
    trace (without it the handler starts a new trace for the resumed run).
    LangChain passes a run's callbacks on to every call inside it, so the
    reviewer, which is called in the answer check, lands in the same trace.
    Langfuse's SDK sends the spans over OpenTelemetry from a thread of its
    own, in batches; a failed export is logged by the SDK and never stops a
    run. Before a batch is sent, `mask_otel_spans` takes the secrets out of
    every text in it: the settings' `redact` (the OpenAI key, Langfuse's
    secret key, the database password) and a key OpenAI shows masked in an
    error ("Incorrect API key provided: sk-proj-****…abcd"), whose start
    and last characters would otherwise reach Langfuse. `span_exporter`
    lets the tests read the spans instead of sending them.
"""

import logging
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langfuse.types import (
    AttributeValue,
    MaskOtelSpansFunction,
    MaskOtelSpansParams,
    MaskOtelSpansResult,
    OtelSpanIdentifier,
    OtelSpanPatch,
)
from opentelemetry.sdk.trace.export import SpanExporter

from avtalsagent.config import Settings

_log = logging.getLogger(__name__)

# A key as OpenAI's 401 shows it: its start, stars, and its last characters.
_MASKED_KEY = re.compile(r"sk-[\w-]*\*{4,}[\w-]*")


@dataclass(frozen=True)
class Tracing:
    """Langfuse's client for the process, or None when tracing is off."""

    client: Langfuse | None = None
    public_key: str | None = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def run_config(
        self,
        *,
        name: str,
        session_id: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> RunnableConfig:
        """The config that traces one question, in a trace of its own; empty when tracing is off.

        Every run of the graph made with it, the question's and those that
        resume it after `ask_user`, goes into the same trace.
        """
        if self.client is None:
            return {}
        trace: dict[str, Any] = {"langfuse_trace_name": name, "langfuse_tags": list(tags)}
        if session_id:
            trace["langfuse_session_id"] = session_id
        handler = CallbackHandler(
            public_key=self.public_key, trace_context={"trace_id": Langfuse.create_trace_id()}
        )
        return {"callbacks": [handler], "metadata": {**(metadata or {}), **trace}}


OFF = Tracing()


def traced(config: RunnableConfig, trace: RunnableConfig) -> RunnableConfig:
    """`config` with a question's tracing (`Tracing.run_config`) in it; `config` itself when off."""
    return merge_configs(config, trace) if trace else config


def tracing_configured(settings: Settings) -> bool:
    """Whether both of Langfuse's keys are set."""
    secret = settings.langfuse_secret_key
    return bool(settings.langfuse_public_key and secret and secret.get_secret_value())


@contextmanager
def open_tracing(
    settings: Settings, span_exporter: SpanExporter | None = None
) -> Iterator[Tracing]:
    """Tracing for the block when Langfuse's keys are set, else `OFF`; flushed on leaving."""
    if not tracing_configured(settings):
        yield OFF
        return
    assert settings.langfuse_secret_key is not None  # tracing_configured checked it
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_base_url,
        span_exporter=span_exporter,
        mask_otel_spans=masking(settings.redact),
    )
    _log.info("tracing to Langfuse at %s", settings.langfuse_base_url)
    try:
        yield Tracing(client, settings.langfuse_public_key)
    finally:
        client.shutdown()


def masking(redact: Callable[[str], str]) -> MaskOtelSpansFunction:
    """Langfuse's `mask_otel_spans`: every text in a batch of spans without its secrets."""

    def mask(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
        patches: dict[OtelSpanIdentifier, OtelSpanPatch | None] = {}
        for identifier, span in params.spans.items():
            changed = {
                name: masked
                for name, value in span.attributes.items()
                if (masked := _masked(value, redact)) != value
            }
            if changed:
                patches[identifier] = OtelSpanPatch(set_attributes=changed)
        return MaskOtelSpansResult(span_patches=patches)

    return mask


def _masked(value: AttributeValue, redact: Callable[[str], str]) -> AttributeValue:
    """`value` with its texts redacted; a list of texts is redacted item by item."""
    if isinstance(value, str):
        return _MASKED_KEY.sub("***", redact(value))
    if isinstance(value, Sequence) and all(isinstance(item, str) for item in value):
        masked = tuple(_MASKED_KEY.sub("***", redact(str(item))) for item in value)
        return value if masked == tuple(value) else masked
    return value
