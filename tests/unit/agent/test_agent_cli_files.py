"""Tests for the command line's `--fil`: the user's own files, attached in a terminal.

`--fil` is passed on once per file; each file is read by the API's rules
into a store for the conversation and named in the terminal, or the command
stops with the API's Swedish reason; `main` with a file answers from it,
and a source in the user's file is printed as "Din fil". The example
contract of the demo is attached as it is, and a PDF is named with its
pages and its warnings.
"""

import hashlib
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from avtalsagent.agent import __main__ as cli
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.schemas import Citation
from avtalsagent.config import Settings
from avtalsagent.uploads.errors import UNSUPPORTED
from tests.unit.agent.scripted_model import ScriptedModel, ScriptedReviewer, final_answer, tool_call
from tests.unit.agent.test_agent_cli import KEY, VERIFIED, Lines, Runs, mcp
from tests.unit.agent.uploaded import CONTRACT, LIABILITY, LIABILITY_QUOTE
from tests.unit.uploads.upload_files import AGREEMENT_PAGES, pdf_bytes

EXAMPLE = Path(__file__).parents[3] / "examples" / "uppladdning" / "exempelavtal-it-konsult.md"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def global_logging_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_configure_logging", lambda level: None)


def terminal() -> tuple[cli.Terminal, io.StringIO]:
    log = io.StringIO()
    return cli.Terminal(out=io.StringIO(), log=log, read_line=Lines()), log


def test_each_file_is_passed_on_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = Runs()
    monkeypatch.setattr(cli, "run", runs)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))

    cli.main(["--fil", "avtal.pdf", "--fil", "bilaga.md", "Jämför"])
    cli.main(["Hur stort är vitet?"])

    assert runs.files == [[Path("avtal.pdf"), Path("bilaga.md")], []]


@pytest.mark.anyio
async def test_a_file_is_read_into_the_conversations_store_and_named(tmp_path: Path) -> None:
    path = tmp_path / "avtal.md"
    path.write_bytes(CONTRACT)
    used, log = terminal()

    store = await cli.attach_files([path], "cli-1", Settings(_env_file=None), used)

    [upload] = await store.list_uploads("cli-1")
    assert (upload.filename, upload.sections) == ("avtal.md", 6)
    assert await store.list_uploads("cli-2") == []
    assert log.getvalue() == "Bifogad fil: avtal.md (text, 6 avsnitt)\n"


@pytest.mark.anyio
async def test_the_example_contract_is_attached_as_it_is() -> None:
    used, log = terminal()

    store = await cli.attach_files([EXAMPLE], "cli-1", Settings(_env_file=None), used)

    [upload] = await store.list_uploads("cli-1")
    assert upload.sections == 10
    assert "Bifogad fil: exempelavtal-it-konsult.md (text, 10 avsnitt)" in log.getvalue()


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("bild.png", b"\x89PNG\r\n", UNSUPPORTED),
        ("saknas.pdf", None, "gick inte att öppna"),
    ],
    ids=["refused", "missing"],
)
@pytest.mark.anyio
async def test_a_file_that_cannot_be_attached_stops_the_command_with_the_reason(
    tmp_path: Path, name: str, content: bytes | None, reason: str
) -> None:
    path = tmp_path / name
    if content is not None:
        path.write_bytes(content)

    with pytest.raises(cli.CommandError) as raised:
        await cli.attach_files([path], "cli-1", Settings(_env_file=None), terminal()[0])

    assert str(raised.value).startswith(f"Filen {path} ")
    assert reason in str(raised.value)


@pytest.mark.anyio
async def test_more_files_than_a_conversation_may_have_stop_the_command(tmp_path: Path) -> None:
    paths = []
    for n in range(2):
        paths.append(tmp_path / f"avtal-{n}.md")
        paths[-1].write_bytes(CONTRACT + f"\nVersion {n}.\n".encode())

    with pytest.raises(cli.CommandError, match="Högst 1 filer"):
        await cli.attach_files(
            paths, "cli-1", Settings(_env_file=None, upload_max_per_thread=1), terminal()[0]
        )


def test_main_answers_from_an_attached_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "avtal.md"
    path.write_bytes(CONTRACT)
    sha256 = hashlib.sha256(CONTRACT).hexdigest()
    citation = {"id": 1, "sha256": sha256, "section_position": LIABILITY, "quote": LIABILITY_QUOTE}
    model = ScriptedModel(
        script=[
            tool_call("list_uploads", {}, "c1"),
            final_answer("Ansvaret är begränsat till 10 procent [1].", [citation], call_id="c2"),
        ]
    )

    @asynccontextmanager
    async def tools(settings: Settings) -> AsyncIterator[McpTools]:
        yield mcp()

    fields: dict[str, Any] = {"openai_api_key": SecretStr(KEY), "checkpointer": "memory"}
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, **fields))
    monkeypatch.setattr(cli, "make_agent_model", lambda settings: model)
    monkeypatch.setattr(cli, "make_reviewer", lambda settings: ScriptedReviewer())
    monkeypatch.setattr(cli, "open_mcp_tools", tools)

    cli.main(["--fil", str(path), "Vad", "säger", "mitt", "avtal?"])

    printed = capsys.readouterr().out
    assert "Bifogad fil: avtal.md (text, 6 avsnitt)" in printed
    assert "→ list_uploads()" in printed
    assert "[1] Din fil: avtal.md, 4 Skadestånd och ansvarsbegränsning ✓" in printed


def test_a_source_in_the_users_file_is_printed_as_din_fil() -> None:
    upload = Citation.model_validate(
        {
            **VERIFIED.model_dump(),
            "file_title": "avtal.pdf",
            "page_title": None,
            "section_number": "7",
            "section_title": "Ansvar",
            "page": 2,
            "source": "upload",
            "upload_id": "0b6c8a2e-3f6b-4c55-9a7e-1d2f3a4b5c6d",
        }
    )

    assert cli.source_lines(upload)[0] == "[1] Din fil: avtal.pdf, 7 Ansvar, s. 2 ✓"


@pytest.mark.anyio
async def test_an_attached_pdf_is_named_with_its_pages_and_warning(tmp_path: Path) -> None:
    path = tmp_path / "avtal.pdf"
    path.write_bytes(pdf_bytes(AGREEMENT_PAGES))
    used, log = terminal()

    store = await cli.attach_files([path], "cli-1", Settings(_env_file=None), used)

    [upload] = await store.list_uploads("cli-1")
    assert cli.attached_lines(upload)[0] == "Bifogad fil: avtal.pdf (pdf, 4 sidor, 5 avsnitt)"
    assert log.getvalue().splitlines() == cli.attached_lines(upload)
    assert "  ! Sidan 3 har ingen text" in log.getvalue()
    one_page = upload.model_copy(update={"pages": 1})
    assert cli.attached_lines(one_page)[0] == "Bifogad fil: avtal.pdf (pdf, 1 sida, 5 avsnitt)"
