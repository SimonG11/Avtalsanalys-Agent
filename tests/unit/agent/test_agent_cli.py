"""Tests for avtalsagent.agent.__main__: the command line.

The parser and every printed line are checked as text. Whole questions run
offline through the real graph, with the scripted model, a fake tool, the
readers and the reviewer of scripted_model.py, and a terminal whose input is
a list.
`main` is run with each setup error: no key, a stdio server that exits at
once, an HTTP server that is not there and a checkpoint database that is
not there; only local processes and connections, no network, key or
database.
"""

import io
import socket
import sys
import urllib.parse
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from mcp import StdioServerParameters
from pydantic import SecretStr

from avtalsagent.agent import __main__ as cli
from avtalsagent.agent import mcp_tools
from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import ANSWER_SUBMITTED, AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.schemas import Answer, Citation, RegisterFact
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import (
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)
from tests.unit.agent.test_agent_graph import BAD, GOOD, NORDLO, SECTION, SHA, search_documents

KEY = "sk-test-not-a-real-key"
OPTIONS = ["IT-drift Större", "IT-drift Mindre"]
VERIFIED = Citation(
    id=1,
    sha256=SHA,
    file_title="Allmänna villkor",
    page_title="IT-drift Större, fler än 200 anställda",
    section_number="6.21.9",
    section_title="Uppsägning",
    page=14,
    quote="uppsägningstid om tre (3) månader",
    verified=True,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Lines:
    """The user's side of a terminal: what is typed, and what the prompts were."""

    def __init__(self, *typed: str) -> None:
        self.typed = list(typed)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.typed.pop(0) if self.typed else None


def terminal(*typed: str) -> tuple[cli.Terminal, io.StringIO, io.StringIO, Lines]:
    out, log, lines = io.StringIO(), io.StringIO(), Lines(*typed)
    return cli.Terminal(out=out, log=log, read_line=lines), out, log, lines


def agent(script: list[AIMessage], *, retries: int = 1) -> tuple[AvtalAgent, ScriptedModel]:
    model = ScriptedModel(script=script)
    settings = Settings(_env_file=None, validation_retries=retries)
    graph = build_agent(
        model,
        mcp(),
        ScriptedReviewer(),
        InMemorySaver(serde=serializer()),
        settings,
    )
    return graph, model


THREAD: RunnableConfig = {"configurable": {"thread_id": "cli"}}


def mcp() -> McpTools:
    """The search tool, the section and the register row of the graph's tests."""
    return McpTools(
        tools=[search_documents], reader=DictReader([SECTION]), register=ListRegister([NORDLO])
    )


# --- the parser -------------------------------------------------------------------------------


@asynccontextmanager
async def no_server(settings: Settings) -> AsyncIterator[McpTools]:
    raise AssertionError("avtal-mcp must not be started")
    yield  # pragma: no cover


class Runs:
    """Stands in for `run`: records what `main` passes on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool, cli.Terminal]] = []

    async def __call__(
        self, settings: Settings, question: str | None, terminal: cli.Terminal, *, as_json: bool
    ) -> None:
        self.calls.append((question, as_json, terminal))


@pytest.fixture(autouse=True)
def global_logging_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    # `main` sets the package's log level, which would last into later tests.
    monkeypatch.setattr(cli, "_configure_logging", lambda level: None)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> Runs:
    runs = Runs()
    monkeypatch.setattr(cli, "run", runs)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
    return runs


@pytest.mark.parametrize(
    ("argv", "question"),
    [
        (["Hur stort är vitet?"], "Hur stort är vitet?"),
        (["Hur", "stort", "är", "vitet?"], "Hur stort är vitet?"),  # without quotes
        ([], None),  # one question after another
    ],
    ids=["quoted", "words", "interactive"],
)
def test_the_question_is_the_words_after_the_command(
    runs: Runs, argv: list[str], question: str | None
) -> None:
    cli.main(argv)

    [(asked, as_json, used)] = runs.calls
    assert (asked, as_json) == (question, False)
    assert used.out is used.log is sys.stdout


def test_json_writes_the_answer_on_stdout_and_the_rest_on_stderr(runs: Runs) -> None:
    cli.main(["--json", "Hur stort är vitet?"])

    [(_, as_json, used)] = runs.calls
    assert as_json
    assert (used.out, used.log) == (sys.stdout, sys.stderr)


def test_a_blank_question_is_refused(runs: Runs, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["  "])

    assert exited.value.code == 2
    assert "frågan är tom" in capsys.readouterr().err
    assert runs.calls == []


# --- the steps --------------------------------------------------------------------------------


def test_a_tool_call_is_one_compact_line() -> None:
    line = cli.call_line(
        "search_documents",
        {
            "query": "uppsägningstid",
            "framework_area": "IT-drift",
            "document_type": None,
            "limit": 5,
        },
    )

    assert line == '→ search_documents(query="uppsägningstid", framework_area="IT-drift", limit=5)'


def test_a_hash_is_shortened_and_a_long_text_cut() -> None:
    long = "ord " * 40

    assert cli.call_line("read_section", {"sha256": SHA, "section_position": 41}) == (
        '→ read_section(sha256="a1a1a1a1a1a1…", section_position=41)'
    )
    assert cli.call_line("search_documents", {"query": long}) == (
        f'→ search_documents(query="{long[:80].rstrip()}…")'
    )


def test_the_models_calls_are_steps_but_ask_user_is_asked_when_the_run_stops() -> None:
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_documents",
                "args": {"query": "vite"},
                "id": "c1",
                "type": "tool_call",
            },
            {"name": "ask_user", "args": {"question": "Vilket?"}, "id": "c2", "type": "tool_call"},
            {"name": "FinalAnswer", "args": {}, "id": "c3", "type": "tool_call"},
        ],
    )

    assert cli.update_lines({"model": {"messages": [message]}}) == [
        '→ search_documents(query="vite")',
        "→ Svaret lämnas för kontroll.",
    ]


def test_a_tool_error_is_shown_without_fastmcps_english_prefix() -> None:
    error = ToolMessage(
        content="Error executing tool read_section: Det finns inget avsnitt med nummer 9.9 i "
        "dokumentet a1a1a1a1a1a1…. Kontrollera numret med get_outline.",
        tool_call_id="c1",
        name="read_section",
        status="error",
    )

    assert cli.message_lines(error) == [
        "  ✗ read_section: Det finns inget avsnitt med nummer 9.9 i dokumentet a1a1a1a1a1a1…. "
        "Kontrollera numret med get_outline."
    ]


def test_the_checks_feedback_lists_its_problems() -> None:
    feedback = ToolMessage(
        content="Kontrollen underkände svaret (försök 1 av 2):\n- Källa [1]: citatet finns inte "
        "ordagrant i avsnitt 6.21.9 (Uppsägning).\nRätta svaret och anropa FinalAnswer igen.",
        tool_call_id="c1",
        name="FinalAnswer",
        status="error",
    )

    assert cli.update_lines({"AnswerCheck.after_agent": {"messages": [feedback]}}) == [
        "  ✗ Kontrollen underkände svaret, som går tillbaka till agenten:",
        "    - Källa [1]: citatet finns inte ordagrant i avsnitt 6.21.9 (Uppsägning).",
    ]


def test_a_handed_in_draft_and_successful_tools_print_nothing_more() -> None:
    submitted = ToolMessage(content=ANSWER_SUBMITTED, tool_call_id="c1", name="FinalAnswer")
    found = ToolMessage(content='{"hits": []}', tool_call_id="c2", name="search_documents")
    refused = ToolMessage(
        content="Error: 1 validation error for FinalAnswer\n Please fix your mistakes.",
        tool_call_id="c3",
        name="FinalAnswer",
    )

    assert (
        cli.update_lines({"model": {"messages": [submitted]}, "tools": {"messages": [found]}}) == []
    )
    assert cli.message_lines(refused) == ["  ✗ Svaret hade fel form och går tillbaka till agenten."]


def test_updates_without_messages_are_skipped() -> None:
    update: dict[str, Any] = {
        "ModelCallLimitMiddleware.before_model": None,
        "AnswerCheck.before_agent": {"answer": None},
        "__interrupt__": (),
    }

    assert cli.update_lines(update) == []


# --- the agent's question -----------------------------------------------------------------------


def test_the_agents_question_shows_its_options_numbered() -> None:
    assert cli.question_lines("Vilket avtal menar du?", OPTIONS) == [
        "? Vilket avtal menar du?",
        "  1. IT-drift Större",
        "  2. IT-drift Mindre",
    ]


@pytest.mark.parametrize(
    ("typed", "options", "reply"),
    [
        ("2", OPTIONS, "IT-drift Mindre"),
        (" 1 ", OPTIONS, "IT-drift Större"),
        ("3", OPTIONS, "3"),  # no such option: the text as written
        ("Båda", OPTIONS, "Båda"),
        ("2", [], "2"),
    ],
    ids=["number", "spaces", "out of range", "own text", "no options"],
)
def test_a_number_picks_an_option_and_other_text_goes_as_written(
    typed: str, options: list[str], reply: str
) -> None:
    assert cli.choice(typed, options) == reply


# --- the answer -------------------------------------------------------------------------------


def test_a_verified_answer_is_printed_with_its_sources() -> None:
    answer = Answer(text="Tre månader [1].", status="verified", citations=[VERIFIED])

    assert cli.answer_lines(answer) == [
        "",
        "Tre månader [1].",
        "",
        "Kontrollerat: varje citat står ordagrant i det avsnitt det anger, och granskningen fann "
        "stöd för svaret.",
        "",
        "Källor:",
        "[1] Allmänna villkor (IT-drift Större, fler än 200 anställda), 6.21.9 Uppsägning, s. 14 ✓",
        "    ”uppsägningstid om tre (3) månader”",
    ]


def test_an_answer_with_reservation_lists_what_could_not_be_checked() -> None:
    failed = VERIFIED.model_copy(update={"verified": False})
    answer = Answer(
        text="Tre månader [1].",
        status="with_reservation",
        citations=[failed],
        reservations=[
            "Källa [1] kunde inte kontrolleras mot avtalstexten.",
            "Svaret kunde inte granskas.",
        ],
    )

    assert cli.answer_lines(answer)[:6] == [
        "",
        "Tre månader [1].",
        "",
        "Med reservation: allt i svaret kunde inte kontrolleras.",
        "  - Källa [1] kunde inte kontrolleras mot avtalstexten.",
        "  - Svaret kunde inte granskas.",
    ]


def test_an_answer_from_the_register_lists_the_agreements_it_was_checked_against() -> None:
    row = RegisterFact(
        agreement_number="23.3-5890-2023-002",
        supplier_name="Nordlo Advance AB",
        org_number="556486-1689",
        sub_area="IT-drift Mindre",
        valid_from=NORDLO.valid_from,
        valid_to=NORDLO.valid_to,
        max_extension_to=None,
    )
    answer = Answer(
        text="Avtal 23.3-5890-2023-002.",
        status="verified",
        citations=[],
        register_facts=[row],
    )

    assert cli.answer_lines(answer) == [
        "",
        "Avtal 23.3-5890-2023-002.",
        "",
        "Kontrollerat: uppgifterna ur registret stämmer, och granskningen fann stöd för svaret.",
        "",
        "Ur registret:",
        "  23.3-5890-2023-002 Nordlo Advance AB (556486-1689), IT-drift Mindre, "
        "2024-11-14–2028-11-13",
    ]


def test_an_agreement_the_register_writes_two_ways_is_one_line() -> None:
    nordlo = RegisterFact(**NORDLO.model_dump(include=set(RegisterFact.model_fields)))
    short = nordlo.model_copy(update={"agreement_number": "23.3-5890-2023-02"})

    assert cli.register_lines([nordlo, short]) == [
        "  23.3-5890-2023-002 Nordlo Advance AB (556486-1689), tidigare EPM Data, 2 delområden, "
        "2024-11-14–2028-11-13",
    ]


def test_an_agreement_with_a_row_per_sub_area_is_one_line() -> None:
    nordlo = RegisterFact(**NORDLO.model_dump(include=set(RegisterFact.model_fields)))
    regions = [nordlo.model_copy(update={"sub_area": f"IT-drift / {n}"}) for n in ("A", "B")]
    extended = nordlo.model_copy(
        update={"agreement_number": "23.3-5890-2023-003", "max_extension_to": date(2029, 11, 13)}
    )
    mixed = [
        extended.model_copy(update={"agreement_number": "23.3-5890-2023-004"}),
        extended.model_copy(
            update={"agreement_number": "23.3-5890-2023-004", "valid_to": date(2027, 1, 1)}
        ),
    ]

    assert cli.register_lines([*regions, extended, *mixed]) == [
        "  23.3-5890-2023-002 Nordlo Advance AB (556486-1689), tidigare EPM Data, 2 delområden, "
        "2024-11-14–2028-11-13",
        "  23.3-5890-2023-003 Nordlo Advance AB (556486-1689), tidigare EPM Data, IT-drift "
        "Mindre, 2024-11-14–2028-11-13, längst till 2029-11-13",
        "  23.3-5890-2023-004 Nordlo Advance AB (556486-1689), tidigare EPM Data, 2 delområden, "
        "olika giltighetstider",
    ]


def test_a_verified_answer_from_both_names_both_checks() -> None:
    answer = Answer(
        text="Tre månader [1].",
        status="verified",
        citations=[VERIFIED],
        register_facts=[RegisterFact(**NORDLO.model_dump(include=set(RegisterFact.model_fields)))],
    )

    assert cli.status_line(answer) == (
        "Kontrollerat: varje citat står ordagrant i det avsnitt det anger och uppgifterna ur "
        "registret stämmer, och granskningen fann stöd för svaret."
    )


def test_no_answer_prints_the_models_words_and_the_status() -> None:
    answer = Answer(text="Avtalen säger inget om det.", status="no_answer", citations=[])

    assert cli.answer_lines(answer) == [
        "",
        "Avtalen säger inget om det.",
        "",
        "Inget svar: det som frågas framgår inte av avtalen eller registret.",
    ]


def test_a_run_that_ends_without_a_draft_says_so_in_the_status() -> None:
    answer = Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[])

    assert cli.status_line(answer) == (
        "Inget svar: agenten kom inte fram till ett svar inom gränsen för modellanrop."
    )


def test_a_source_without_page_or_number_and_one_that_could_not_be_read() -> None:
    word_file = VERIFIED.model_copy(
        update={"page_title": None, "section_number": None, "page": None}
    )
    unread = Citation(
        id=2,
        sha256="b2" * 32,
        file_title="",
        page_title=None,
        section_number=None,
        section_title="",
        page=None,
        quote="tre månader",
        verified=False,
    )

    assert cli.source_lines(word_file)[0] == "[1] Allmänna villkor, Uppsägning ✓"
    assert cli.source_lines(unread) == [
        "[2] avsnittet kunde inte läsas (dokument b2b2b2b2b2b2…) ✗",
        "    ”tre månader”",
    ]


def test_json_is_the_answer_as_the_web_app_gets_it() -> None:
    answer = Answer(text="Tre månader [1].", status="verified", citations=[VERIFIED])

    assert Answer.model_validate_json(cli.answer_json(answer)) == answer
    assert "”" not in cli.answer_json(answer) and "Allmänna" in cli.answer_json(answer)


# --- a question through the graph -------------------------------------------------------------


@pytest.mark.anyio
async def test_a_question_prints_its_steps_and_the_users_reply_resumes_the_run() -> None:
    graph, model = agent(
        [
            tool_call("search_documents", {"query": "uppsägningstid"}, "c1"),
            tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": OPTIONS}, "c2"),
            final_answer("Tre månader [1].", [BAD], call_id="c3"),
            final_answer("Tre månader [1].", [GOOD], call_id="c4"),
        ]
    )
    used, _, log, lines = terminal("", "2")

    answer = await cli.ask(graph, "Vilken uppsägningstid gäller?", THREAD, used)

    assert answer.status == "verified"
    steps = log.getvalue().splitlines()
    assert steps[:4] == [
        '→ search_documents(query="uppsägningstid")',
        "? Vilket avtal menar du?",
        "  1. IT-drift Större",
        "  2. IT-drift Mindre",
    ]
    assert steps[4:] == [
        "→ Svaret lämnas för kontroll.",
        "  ✗ Kontrollen underkände svaret, som går tillbaka till agenten:",
        "    - Källa [1]: citatet finns inte ordagrant i avsnitt 6.21.9 (Uppsägning). Kopiera "
        "det ur texten från read_section, utan utelämningar och utan egna ord.",
        "→ Svaret lämnas för kontroll.",
    ]
    # A blank line is asked again; "2" is the second option.
    assert lines.prompts == ["Svar (nummer eller egen text): "] * 2
    [reply] = [m for m in model.calls[2] if isinstance(m, ToolMessage) and m.name == "ask_user"]
    assert reply.content == "IT-drift Mindre"


@pytest.mark.anyio
async def test_without_input_for_the_agents_question_the_command_stops() -> None:
    graph, _ = agent([tool_call("ask_user", {"question": "Vilket avtal menar du?"}, "c1")])
    used, _, _, lines = terminal()

    with pytest.raises(cli.CommandError, match="inmatningen tog slut"):
        await cli.ask(graph, "Vilken uppsägningstid gäller?", THREAD, used)
    assert lines.prompts == ["Svar: "]


@pytest.mark.anyio
async def test_follow_up_questions_keep_the_conversation_until_the_input_ends() -> None:
    graph, model = agent(
        [
            final_answer("Tre månader [1].", [GOOD], call_id="c1"),
            final_answer("Det framgår inte.", answered=False, call_id="c2"),
        ]
    )
    used, out, log, lines = terminal("Vilken uppsägningstid gäller?", "  ", "Och för Mindre?")

    await cli.converse(graph, None, used, as_json=False)

    assert lines.prompts == [cli.QUESTION_PROMPT] * 4
    assert log.getvalue().startswith(cli.WELCOME)
    printed = out.getvalue()
    assert "Kontrollerat: " in printed and "Inget svar: " in printed
    # The second question's model call has the first question and its answer before it.
    questions = [m.content for m in model.calls[1] if m.type == "human"]
    assert questions == ["Vilken uppsägningstid gäller?", "Och för Mindre?"]


@pytest.mark.anyio
async def test_json_prints_only_the_answer_on_its_stream() -> None:
    graph, _ = agent([final_answer("Tre månader [1].", [GOOD], call_id="c1")])
    used, out, log, _ = terminal()

    await cli.converse(graph, "Vilken uppsägningstid gäller?", used, as_json=True)

    answer = Answer.model_validate_json(out.getvalue())
    assert answer.status == "verified"
    assert log.getvalue() == "→ Svaret lämnas för kontroll.\n"


# --- main: an answer, and the setup errors ------------------------------------------------------


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The fields of the Settings `main` reads; a test changes them before it runs."""
    fields: dict[str, Any] = {
        "openai_api_key": SecretStr(KEY),
        "mcp_transport": "stdio",
        "checkpointer": "memory",
    }
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, **fields))
    yield fields


def test_main_answers_a_question_and_ends_normally(
    settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @asynccontextmanager
    async def tools(settings: Settings) -> AsyncIterator[McpTools]:
        yield mcp()

    model = ScriptedModel(script=[final_answer("Tre månader [1].", [GOOD], call_id="c1")])
    monkeypatch.setattr(cli, "make_agent_model", lambda settings: model)
    monkeypatch.setattr(cli, "make_reviewer", lambda settings: ScriptedReviewer())
    monkeypatch.setattr(cli, "open_mcp_tools", tools)

    cli.main(["Vilken", "uppsägningstid", "gäller?"])

    printed = capsys.readouterr().out
    assert "Tre månader [1]." in printed
    assert "[1] Allmänna villkor (IT-drift Större, fler än 200 anställda)" in printed


def test_without_a_key_main_stops_before_starting_avtal_mcp(
    settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings["openai_api_key"] = None
    monkeypatch.setattr(cli, "open_mcp_tools", no_server)

    with pytest.raises(SystemExit) as exited:
        cli.main(["Hur stort är vitet?"])

    assert exited.value.code == 1
    assert "OPENAI_API_KEY saknas" in capsys.readouterr().err


def test_a_stdio_server_that_exits_at_once_is_a_setup_error(
    settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        mcp_tools,
        "stdio_parameters",
        lambda: StdioServerParameters(command=sys.executable, args=["-c", "raise SystemExit(3)"]),
    )

    with pytest.raises(SystemExit) as exited:
        cli.main(["Hur stort är vitet?"])

    err = capsys.readouterr().err
    assert exited.value.code == 1
    assert "avtal-mcp startade inte (MCP_TRANSPORT=stdio" in err
    assert "McpError: Connection closed" in err
    assert KEY not in err


def free_port() -> int:
    """A local port nothing listens on: bound, read and closed again."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_an_http_server_that_is_not_there_is_a_setup_error(
    settings: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"http://127.0.0.1:{free_port()}/mcp"
    settings.update(mcp_transport="streamable_http", mcp_url=url)

    with pytest.raises(SystemExit) as exited:
        cli.main(["Hur stort är vitet?"])

    err = capsys.readouterr().err
    assert exited.value.code == 1
    assert f"avtal-mcp svarar inte på {url}" in err
    assert "ConnectError" in err


@pytest.mark.parametrize("password", ["hemligt-ord", "Sommar50%off!"], ids=["plain", "a bare %"])
def test_a_checkpoint_database_that_is_not_there_is_a_setup_error_without_its_password(
    password: str,
    settings: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @asynccontextmanager
    async def tools(settings: Settings) -> AsyncIterator[McpTools]:
        yield mcp()

    monkeypatch.setattr(cli, "open_mcp_tools", tools)
    settings.update(
        checkpointer="postgres",
        database_url=f"postgresql+psycopg://agent:{password}@127.0.0.1:{free_port()}/avtal",
    )

    with pytest.raises(SystemExit) as exited:
        cli.main(["Hur stort är vitet?"])

    err = capsys.readouterr().err
    assert exited.value.code == 1
    assert "Agentens checkpoints (CHECKPOINTER=postgres) gick inte att öppna" in err
    assert password not in err


# --- describe_failure ---------------------------------------------------------------------------


def openai_error(kind: type[openai.APIStatusError], status: int, message: str) -> Exception:
    # The error reads only these attributes of the HTTP response.
    response = SimpleNamespace(request=None, status_code=status, headers={})
    return kind(message, response=cast(Any, response), body=None)


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (ExceptionGroup("", [cli.CommandError("Agenten väntar")]), 1, "Agenten väntar"),
        (BaseExceptionGroup("", [KeyboardInterrupt()]), 130, "\nAvbrutet."),
        (KeyboardInterrupt(), 130, "\nAvbrutet."),
        (
            ExceptionGroup("", [openai_error(openai.AuthenticationError, 401, f"Bad key {KEY}")]),
            1,
            "OpenAI tog inte emot nyckeln i OPENAI_API_KEY.",
        ),
        (
            openai_error(openai.RateLimitError, 429, f"Slow down, {KEY}"),
            1,
            "OpenAI svarade med ett fel (RateLimitError): Slow down, ***",
        ),
        (
            # Over streamable HTTP the transport's task group raises httpx's own error.
            ExceptionGroup("", [httpx.ConnectError("All connection attempts failed")]),
            1,
            "Förbindelsen med avtal-mcp (http://localhost:8001/mcp) bröts: ConnectError: "
            "All connection attempts failed",
        ),
    ],
    ids=["command", "ctrl-c in a group", "ctrl-c", "key refused", "rate limit", "http"],
)
def test_the_errors_the_user_can_act_on_have_a_message_without_the_key(
    error: BaseException, code: int, message: str
) -> None:
    settings = Settings(_env_file=None, openai_api_key=SecretStr(KEY))

    assert cli.describe_failure(error, settings) == (code, message)


@pytest.mark.parametrize(
    "password",
    ["Sommar50%off!", "p%40ss%3Aw%2Frd", "hemligt-ord"],
    ids=["a bare %", "percent-encoded", "plain"],
)
def test_the_database_password_is_hidden_in_every_form_an_error_quotes(password: str) -> None:
    settings = Settings(_env_file=None, database_url=f"postgresql+psycopg://u:{password}@db/x")
    decoded = urllib.parse.unquote(password)
    encoded = urllib.parse.quote(decoded, safe="")
    error = cli.CommandError(
        f'invalid token: "{password}"; "{decoded}"; postgresql://u:{encoded}@db/x; '
        f"postgresql://u:{password}@db/x"
    )

    code, message = cli.describe_failure(error, settings) or (0, "")

    assert code == 1
    assert message == (
        'invalid token: "***"; "***"; postgresql://u:***@db/x; postgresql://u:***@db/x'
    )


def test_the_database_password_is_hidden_where_an_address_holds_it() -> None:
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://avtalsagent:avtalsagent@db:5432/avtal"
    )
    error = cli.CommandError(
        "postgresql://avtalsagent:avtalsagent@db:5432/avtal svarar inte; starta "
        "avtalsagent.mcp_server"
    )

    assert cli.describe_failure(error, settings) == (
        1,
        "postgresql://avtalsagent:***@db:5432/avtal svarar inte; starta avtalsagent.mcp_server",
    )


def test_any_other_error_is_raised_with_its_traceback() -> None:
    error = ExceptionGroup("", [ValueError("a bug")])

    assert cli.describe_failure(error, Settings(_env_file=None)) is None
