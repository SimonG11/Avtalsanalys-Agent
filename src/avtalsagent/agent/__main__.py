"""Command line: ask the agent about the framework agreements in a terminal.

What:
    `python -m avtalsagent.agent "fråga"` asks one question and prints the
    answer; without a question it reads one question after another, in one
    conversation, until Ctrl-D. While the agent works, each tool call is
    printed with its arguments; a question from the agent (`ask_user`) is
    asked in the terminal, its options numbered. The answer is printed with
    its status (Kontrollerat, Med reservation, Inget svar), what could not
    be checked, its sources, each marked ✓ or ✗ by the citation check, and
    the register rows its facts were checked against. `--json` prints the
    answer as the web app gets it, the state's `answer`, and the steps on
    stderr. `--fil PATH`, once per file, attaches the user's own files to
    the conversation, as an upload in the web app does, so the agent can
    compare them with the agreements; a source in such a file is printed
    as "Din fil".

Why:
    The command line is the agent's fallback in a demo and the quickest way
    to try a prompt or a tool by hand, without the API and the web app (M9).
    It runs the same graph, with the same check, as the API will; only the
    checkpointer (memory) and the way the user answers differ. The messages
    are in Swedish, for the people who ask. The attached files are read by
    the API's rules and limits (`uploads/local_file.py`), in a child
    process as there, into a store in memory, for this run only, so
    `--fil` is also the demo's fallback for the comparison when the web app
    is not there (ADR 0026).

How:
    Starts the model clients (`make_agent_model` and `make_reviewer`;
    without OPENAI_API_KEY it stops before anything else), avtal-mcp (`open_mcp_tools`: over stdio a
    child process, over streamable HTTP the server at MCP_URL), the
    checkpointer (`open_checkpointer`) and the tracing (`open_tracing`: off
    without Langfuse's keys; with them each question is a trace, the
    user's answers to the agent's questions included, and the conversation
    its session), and builds the graph (`build_agent`; with `--fil`, on
    the store that holds the files for the conversation's thread). Each
    question runs with `astream` in "updates" mode, so a tool call is
    printed when the model makes it. A run that stops at
    `ask_user` is resumed with `Command(resume=...)`: a number picks that
    option, other text goes as written. Follow-up questions use the same
    thread, so the agent keeps the conversation. The exit code is 0 when
    the question got an answer, whatever its status; 1 when the agent could
    not start (no key, avtal-mcp or the checkpoint database not reached) or
    a question failed, with a message that says why; 2 for wrong arguments
    (argparse); 130 on Ctrl-C. Error messages never show the OpenAI key or
    the database password.
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import anyio
import httpx
import openai
from langchain.agents.middleware import InputAgentState
from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Interrupt
from mcp.shared.exceptions import McpError

from avtalsagent.agent.ask_user import ask_user
from avtalsagent.agent.checkpointer import open_checkpointer
from avtalsagent.agent.graph import ANSWER_SUBMITTED, AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import open_mcp_tools
from avtalsagent.agent.middleware import FINAL_ANSWER_TOOL, NO_DRAFT_TEXT
from avtalsagent.agent.model import MissingApiKeyError, make_agent_model
from avtalsagent.agent.reviewer import make_reviewer
from avtalsagent.agent.schemas import Answer, Citation, RegisterFact
from avtalsagent.config import Settings, get_settings
from avtalsagent.domain.identifiers import agreement_key
from avtalsagent.domain.uploads import Upload
from avtalsagent.observability.tracing import OFF as TRACING_OFF
from avtalsagent.observability.tracing import Tracing, open_tracing, traced
from avtalsagent.uploads.errors import UploadRejected
from avtalsagent.uploads.local_file import read_local_file
from avtalsagent.uploads.parse import UploadLimits
from avtalsagent.uploads.parse_process import ProcessParser
from avtalsagent.uploads.store import MemoryUploadStore, TooManyUploads, retention

STATUS_NAMES = {
    "verified": "Kontrollerat",
    "with_reservation": "Med reservation",
    "no_answer": "Inget svar",
}
WELCOME = (
    "Avtalsagenten svarar på frågor om Statens inköpscentrals ramavtal. Följdfrågor "
    "fortsätter samma samtal; avsluta med Ctrl-D."
)
QUESTION_PROMPT = "\nFråga: "
INTERRUPTED = 130  # the shell's code for a command ended by Ctrl-C (128 + SIGINT)

# How long an argument or an error may be in a step line before it is cut.
_VALUE_CHARS = 80
_ERROR_CHARS = 200
# FastMCP writes this in English before every tool error's own (Swedish) text.
_MCP_ERROR_PREFIX = "Error executing tool "
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class CommandError(Exception):
    """The agent could not start or could not finish a question; the message says why."""


@dataclass(frozen=True)
class Terminal:
    """Where the command line writes and reads: the answers, and everything else."""

    out: TextIO  # the answers
    log: TextIO  # the steps, the agent's questions and the prompts
    read_line: Callable[[str], str | None]  # a line typed after the prompt; None at the end

    def answer(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    def say(self, text: str) -> None:
        print(text, file=self.log, flush=True)


def build_parser() -> argparse.ArgumentParser:
    """The question and the options."""
    parser = argparse.ArgumentParser(
        prog="python -m avtalsagent.agent",
        description=(
            "Ställ en fråga om ramavtalen till agenten. Utan fråga läser den en fråga i taget, "
            "i samma samtal, tills du avslutar med Ctrl-D."
        ),
    )
    parser.add_argument(
        "question",
        nargs="*",
        metavar="fråga",
        help='frågan, med eller utan citattecken, t.ex. "Hur stort är vitet i IT-drift Mindre?"',
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="skriv svaret som JSON, som webbappen får det (answer); stegen går till stderr",
    )
    parser.add_argument(
        "--fil",
        action="append",
        type=Path,
        default=[],
        dest="files",
        metavar="FIL",
        help=(
            "bifoga en egen fil (PDF med text, .docx, .txt eller .md) som agenten kan läsa och "
            "jämföra med ramavtalen; ange --fil en gång per fil"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    question = " ".join(args.question).strip() if args.question else None
    if question == "":
        parser.error("frågan är tom")
    settings = get_settings()
    _configure_logging(settings.log_level)
    log = sys.stderr if args.json else sys.stdout
    terminal = Terminal(out=sys.stdout, log=log, read_line=lambda prompt: _read_line(prompt, log))
    try:
        asyncio.run(run(settings, question, terminal, as_json=args.json, files=args.files))
    except BaseException as error:  # Ctrl-C and the errors the user can act on; others go on
        failure = describe_failure(error, settings)
        if failure is None:
            raise
        code, message = failure
        print(message, file=sys.stderr, flush=True)
        raise SystemExit(code) from None


async def run(
    settings: Settings,
    question: str | None,
    terminal: Terminal,
    *,
    as_json: bool,
    files: Sequence[Path] = (),
) -> None:
    """Start the agent and answer `question`, or each question typed, until the input ends.

    `files` are attached to the conversation before the first question.
    """
    model = make_agent_model(settings)  # without a key, before a server is started
    reviewer = make_reviewer(settings)
    thread_id = f"cli-{uuid.uuid4()}"
    uploads = await attach_files(files, thread_id, settings, terminal) if files else None
    async with AsyncExitStack() as stack:
        tracing = stack.enter_context(open_tracing(settings))  # closed last: sends what is left
        try:
            mcp = await stack.enter_async_context(open_mcp_tools(settings))
        except Exception as error:
            raise CommandError(_mcp_failed(settings, error)) from error
        try:
            checkpointer = await stack.enter_async_context(open_checkpointer(settings))
        except Exception as error:
            raise CommandError(
                f"Agentens checkpoints (CHECKPOINTER={settings.checkpointer}) gick inte att "
                f"öppna: {_causes(error)}"
            ) from error
        graph = build_agent(model, mcp, reviewer, checkpointer, settings, uploads=uploads)
        await converse(
            graph, question, terminal, as_json=as_json, tracing=tracing, thread_id=thread_id
        )


async def attach_files(
    paths: Sequence[Path], thread_id: str, settings: Settings, terminal: Terminal
) -> MemoryUploadStore:
    """A store in memory with the files read into sections for the thread, each one named."""
    store = MemoryUploadStore(retention(settings))
    limits = UploadLimits.from_settings(settings)
    parser = ProcessParser(workers=1)
    try:
        for path in paths:
            try:
                new = await read_local_file(path, thread_id, limits, parser)
            except UploadRejected as error:
                raise CommandError(f"Filen {path} kunde inte bifogas: {error.detail}") from None
            except OSError as error:
                raise CommandError(
                    f"Filen {path} gick inte att öppna: {error.strerror or error}"
                ) from None
            try:
                upload, _ = await store.add_upload(new, settings.upload_max_per_thread)
            except TooManyUploads:
                raise CommandError(
                    f"Högst {settings.upload_max_per_thread} filer kan bifogas i ett samtal."
                ) from None
            for line in attached_lines(upload):
                terminal.say(line)
    finally:
        parser.close()
    return store


async def converse(
    graph: AvtalAgent,
    question: str | None,
    terminal: Terminal,
    *,
    as_json: bool,
    tracing: Tracing = TRACING_OFF,
    thread_id: str | None = None,
) -> None:
    """Answer `question`, or else each question the user types, in one conversation.

    `thread_id` is the conversation's; a new one when None.
    """
    thread_id = thread_id or f"cli-{uuid.uuid4()}"
    thread: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    def config() -> RunnableConfig:  # each question its own trace, the conversation its session
        trace = tracing.run_config(name="fråga", session_id=thread_id, tags=["cli"])
        return traced(thread, trace)

    if question is not None:
        _show(await ask(graph, question, config(), terminal), terminal, as_json=as_json)
        return
    terminal.say(WELCOME)
    while (line := terminal.read_line(QUESTION_PROMPT)) is not None:
        if line.strip():
            _show(await ask(graph, line.strip(), config(), terminal), terminal, as_json=as_json)
    terminal.say("")  # after Ctrl-D, so the shell's prompt starts on a line of its own


async def ask(
    graph: AvtalAgent, question: str, config: RunnableConfig, terminal: Terminal
) -> Answer:
    """Run one question to its answer: print the steps, put the agent's questions to the user."""
    run_input: InputAgentState | Command[Any] = {
        "messages": [{"role": "user", "content": question}]
    }
    while True:
        async for update in graph.astream(run_input, config, stream_mode="updates"):
            for line in update_lines(update):
                terminal.say(line)
        state = await graph.aget_state(config)
        if not state.interrupts:
            break
        run_input = Command(resume=_replies(state.interrupts, terminal))
    answer = state.values.get("answer")
    if answer is None:  # the check sets an answer at the end of every run
        raise CommandError("Agenten avslutade frågan utan svar.")
    return Answer.model_validate(answer)


def _replies(interrupts: Sequence[Interrupt], terminal: Terminal) -> Any:
    """The user's answers to the agent's questions, as `Command(resume=...)` takes them."""
    replies = {interrupt.id: _reply(interrupt.value, terminal) for interrupt in interrupts}
    # One question takes the answer itself; several (parallel ask_user calls), one per id.
    return next(iter(replies.values())) if len(replies) == 1 else replies


def _reply(payload: Any, terminal: Terminal) -> str:
    """Ask the user the agent's question and read the answer: an option's number or text."""
    fields = payload if isinstance(payload, Mapping) else {"question": payload}
    options = [str(option) for option in fields.get("options") or []]
    for text in question_lines(str(fields.get("question")), options):
        terminal.say(text)
    prompt = "Svar (nummer eller egen text): " if options else "Svar: "
    while (line := terminal.read_line(prompt)) is not None:
        if line.strip():
            return choice(line, options)
    raise CommandError("Agenten väntar på ett svar på sin fråga, men inmatningen tog slut.")


# --- what is printed --------------------------------------------------------------------------


def update_lines(update: Mapping[str, Any]) -> list[str]:
    """The step lines of one graph update (`astream` in "updates" mode): node -> its update."""
    lines: list[str] = []
    for node_update in update.values():
        if isinstance(node_update, Mapping):  # an interrupt's update is a tuple; skipped here
            for message in node_update.get("messages", []):
                lines += message_lines(message)
    return lines


def message_lines(message: BaseMessage) -> list[str]:
    """What the user sees of one message while the agent works."""
    if isinstance(message, AIMessage):
        # ask_user is not a step: its question is asked when the run stops for it.
        return [_step(call) for call in message.tool_calls if call["name"] != ask_user.name]
    if not isinstance(message, ToolMessage):
        return []
    if message.name == FINAL_ANSWER_TOOL:
        if message.status == "error":  # the check's feedback in place of the draft's result
            return [
                "  ✗ Kontrollen underkände svaret, som går tillbaka till agenten:",
                *(f"    {line}" for line in message.text.splitlines() if line.startswith("- ")),
            ]
        if message.text != ANSWER_SUBMITTED:  # a draft the schema refused
            return ["  ✗ Svaret hade fel form och går tillbaka till agenten."]
        return []
    if message.status == "error":
        return [f"  ✗ {message.name}: {_error_text(message.text, message.name)}"]
    return []


def _step(call: ToolCall) -> str:
    if call["name"] == FINAL_ANSWER_TOOL:
        return "→ Svaret lämnas för kontroll."
    return call_line(call["name"], call["args"])


def call_line(name: str, args: Mapping[str, Any]) -> str:
    """A tool call, compact: `→ read_section(sha256="28ca7018a608…", section_position=41)`.

    Arguments the model left empty (None) are not shown; the model often
    sends every optional argument.
    """
    listed = ", ".join(f"{key}={_value(value)}" for key, value in args.items() if value is not None)
    return f"→ {name}({listed})"


def _value(value: Any) -> str:
    """An argument as JSON, a sha256 shortened and a long text cut."""
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        value = f"{value[:12]}…"
    elif isinstance(value, str) and len(value) > _VALUE_CHARS:
        value = f"{value[:_VALUE_CHARS].rstrip()}…"
    return json.dumps(value, ensure_ascii=False)


def _error_text(text: str, tool: str | None) -> str:
    """A tool's error as the user reads it: without FastMCP's English prefix, one line, cut."""
    prefix = f"{_MCP_ERROR_PREFIX}{tool}: "
    text = " ".join(text.removeprefix(prefix).split())
    return text if len(text) <= _ERROR_CHARS else f"{text[:_ERROR_CHARS].rstrip()}…"


def question_lines(question: str, options: Sequence[str]) -> list[str]:
    """The agent's question to the user, with its options numbered."""
    return [f"? {question}", *(f"  {n}. {option}" for n, option in enumerate(options, start=1))]


def choice(text: str, options: Sequence[str]) -> str:
    """The user's answer: the option a number names, or else the text as written."""
    text = text.strip()
    if text.isdigit() and 1 <= int(text) <= len(options):
        return options[int(text) - 1]
    return text


def answer_lines(answer: Answer) -> list[str]:
    """The answer as printed: the text, the status and its reservations, the sources and rows."""
    lines = ["", answer.text, "", status_line(answer)]
    lines += [f"  - {note}" for note in answer.reservations]
    if answer.citations:
        lines += ["", "Källor:"]
        for citation in answer.citations:
            lines += source_lines(citation)
    if answer.register_facts:
        lines += ["", "Ur registret:", *register_lines(answer.register_facts)]
    return lines


def status_line(answer: Answer) -> str:
    """The answer's status in Swedish, with what it means."""
    name = STATUS_NAMES[answer.status]
    if answer.status == "verified":
        checked = []
        if answer.citations:
            checked.append("varje citat står ordagrant i det avsnitt det anger")
            checked.append("ändringar som säkert gäller ett citerat avsnitt är också citerade")
        if answer.register_facts:
            checked.append("uppgifterna ur registret stämmer")
        listed = ", ".join(checked[:-1]) + f" och {checked[-1]}" if len(checked) > 1 else checked[0]
        return f"{name}: {listed}, och granskningen fann stöd för svaret."
    if answer.status == "no_answer" and answer.text == NO_DRAFT_TEXT:
        return f"{name}: agenten kom inte fram till ett svar inom gränsen för modellanrop."
    if answer.status == "no_answer":
        return f"{name}: det som frågas framgår inte av avtalen eller registret."
    return f"{name}: allt i svaret kunde inte kontrolleras."


def attached_lines(upload: Upload) -> list[str]:
    """An attached file: `Bifogad fil: avtal.pdf (pdf, 3 sidor, 9 avsnitt)` and its warnings."""
    details = [upload.kind.value]
    if upload.pages is not None:
        details.append("1 sida" if upload.pages == 1 else f"{upload.pages} sidor")
    details.append(f"{upload.sections} avsnitt")
    lines = [f"Bifogad fil: {upload.filename} ({', '.join(details)})"]
    return lines + [f"  ! {warning}" for warning in upload.warnings]


def source_lines(citation: Citation) -> list[str]:
    """One source: `[1] Allmänna villkor (avtalssida), 6.21.9 Uppsägning, s. 14 ✓` and its quote.

    A source in the user's own file is `[1] Din fil: avtal.pdf, 7 Ansvar, s. 2 ✓`.
    """
    mark = "✓" if citation.verified else "✗"
    if not citation.file_title:  # the check could not read the section
        where = f"avsnittet kunde inte läsas (dokument {citation.sha256[:12]}…)"
    else:
        where = citation.file_title
        if citation.source == "upload":
            where = f"Din fil: {where}"
        if citation.page_title:
            where += f" ({citation.page_title})"
        heading = " ".join(filter(None, (citation.section_number, citation.section_title)))
        if heading:
            where += f", {heading}"
        if citation.page is not None:
            where += f", s. {citation.page}"
    return [f"[{citation.id}] {where} {mark}", f"    ”{citation.quote}”"]


def register_lines(facts: Sequence[RegisterFact]) -> list[str]:
    """The register rows, one line per agreement: an agreement has a row per sub-area.

    The register writes some numbers two ways (-001 and -01); they are one agreement.
    """
    agreements: dict[str, list[RegisterFact]] = {}
    for fact in facts:
        key = agreement_key(fact.agreement_number) or fact.agreement_number
        agreements.setdefault(key, []).append(fact)
    return [agreement_line(rows) for rows in agreements.values()]


def agreement_line(rows: Sequence[RegisterFact]) -> str:
    """`23.3-5890-2023-002 Nordlo Advance AB (556486-1689), IT-drift …, 2024-11-14–2028-11-13`."""
    first = rows[0]
    supplier = f"{first.supplier_name} ({first.org_number})"
    if first.former_names:
        supplier += f", tidigare {', '.join(first.former_names)}"
    where = first.sub_area if len(rows) == 1 else f"{len(rows)} delområden"
    periods = {(row.valid_from, row.valid_to, row.max_extension_to) for row in rows}
    if len(periods) > 1:
        return f"  {first.agreement_number} {supplier}, {where}, olika giltighetstider"
    period = f"{first.valid_from}–{first.valid_to}"
    if first.max_extension_to is not None:
        period += f", längst till {first.max_extension_to}"
    return f"  {first.agreement_number} {supplier}, {where}, {period}"


def answer_json(answer: Answer) -> str:
    """The answer as the web app gets it: the state's `answer` (webbapp-kontrakt.md)."""
    return json.dumps(answer.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _show(answer: Answer, terminal: Terminal, *, as_json: bool) -> None:
    if as_json:
        terminal.answer(answer_json(answer))
    else:
        for line in answer_lines(answer):
            terminal.answer(line)


# --- errors -----------------------------------------------------------------------------------


def describe_failure(error: BaseException, settings: Settings) -> tuple[int, str] | None:
    """The exit code and message for an error that ended the command; None for a bug.

    Errors raised inside the MCP client's task groups come wrapped in
    exception groups, so the group's leaves are what is looked at.
    """
    leaves = list(_leaves(error))
    if any(isinstance(leaf, KeyboardInterrupt) for leaf in leaves):
        return INTERRUPTED, "\nAvbrutet."
    for leaf in leaves:
        if isinstance(leaf, CommandError | MissingApiKeyError):
            return 1, settings.redact(str(leaf))
        if isinstance(leaf, openai.AuthenticationError):  # its message shows part of the key
            return 1, "OpenAI tog inte emot nyckeln i OPENAI_API_KEY."
        if isinstance(leaf, openai.OpenAIError):
            return 1, settings.redact(f"OpenAI svarade med ett fel ({type(leaf).__name__}): {leaf}")
        if isinstance(leaf, McpError | anyio.ClosedResourceError | anyio.BrokenResourceError):
            return 1, settings.redact(f"Förbindelsen med avtal-mcp bröts: {_causes(leaf)}")
        if isinstance(leaf, httpx.HTTPError):  # over streamable HTTP, a request that failed
            return 1, settings.redact(
                f"Förbindelsen med avtal-mcp ({settings.mcp_url}) bröts: {_causes(leaf)}"
            )
    return None


def _mcp_failed(settings: Settings, error: BaseException) -> str:
    """Why the agent has no tools, and what to do."""
    if settings.mcp_transport == "stdio":
        return (
            "avtal-mcp startade inte (MCP_TRANSPORT=stdio, python -m avtalsagent.mcp_server "
            f"stdio): {_causes(error)}. Serverns eget fel står ovanför, om det finns något."
        )
    return (
        f"avtal-mcp svarar inte på {settings.mcp_url} (MCP_TRANSPORT=streamable_http): "
        f"{_causes(error)}. Starta servern med uv run python -m avtalsagent.mcp_server http, "
        "eller sätt MCP_TRANSPORT=stdio."
    )


def _causes(error: BaseException) -> str:
    """The error's leaves as text: `ConnectError: All connection attempts failed`."""
    return "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in _leaves(error))


def _leaves(error: BaseException) -> Iterator[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
    else:
        yield error


# --- the terminal -----------------------------------------------------------------------------


def _read_line(prompt: str, log: TextIO) -> str | None:
    """A line typed in the terminal, None at the end of the input (Ctrl-D).

    asyncio's runner turns the first Ctrl-C into a cancellation of the run,
    which a blocking `input()` would not see until Enter was pressed; while
    it waits for a line, Ctrl-C raises KeyboardInterrupt at once instead.
    """
    log.write(prompt)
    log.flush()
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        return input()
    except EOFError:
        return None
    finally:
        signal.signal(signal.SIGINT, previous)


def _configure_logging(level: str) -> None:
    """This package's log lines at LOG_LEVEL, other libraries' warnings only (on stderr)."""
    logging.basicConfig(format=_LOG_FORMAT, level=logging.WARNING)
    logging.getLogger("avtalsagent").setLevel(level.upper())


if __name__ == "__main__":
    main()
