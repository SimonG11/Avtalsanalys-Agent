"""Tests for avtalsagent.api.documents: the route that serves a cited document's PDF.

The route is called through the app with a `DocumentFiles` stand-in, so
each case (shown, held back or unknown, a Word file, missing on disk, the
database down, a malformed hash) is one lookup result. The lookup against
Postgres is tested in tests/integration/test_api_document_lookup.py.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.exc import OperationalError

from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.api.app import create_app
from avtalsagent.api.documents import (
    DATABASE_DOWN,
    NOT_FOUND,
    NOT_ON_DISK,
    WORD_FILE,
    DocumentFiles,
    StoredFile,
)
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
)

SHA = "c3" * 32
PDF = b"%PDF-1.7\n% a stand-in\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Files:
    """A lookup that answers `found`, or raises `error`, and records what it was asked."""

    def __init__(self, found: StoredFile | None = None, error: Exception | None = None) -> None:
        self.found = found
        self.error = error
        self.asked: list[str] = []

    def find(self, sha256: str) -> StoredFile | None:
        self.asked.append(sha256)
        if self.error is not None:
            raise self.error
        return self.found


def app_with(files: Files) -> FastAPI:
    @asynccontextmanager
    async def open_tools(settings: Settings) -> AsyncIterator[McpTools]:
        yield McpTools(
            tools=[], reader=DictReader([]), register=ListRegister(), amendments=DictAmendments()
        )

    @asynccontextmanager
    async def open_saver(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
        yield InMemorySaver()

    @contextmanager
    def open_documents(settings: Settings) -> Iterator[DocumentFiles]:
        yield files

    return create_app(
        Settings(_env_file=None),
        make_model=lambda settings: ScriptedModel(script=[]),
        make_answer_reviewer=lambda settings: ScriptedReviewer(),
        open_tools=open_tools,
        open_saver=open_saver,
        open_documents=open_documents,
    )


async def get(files: Files, sha256: str = SHA) -> httpx.Response:
    app = app_with(files)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(f"/api/documents/{sha256}/pdf")


@pytest.mark.anyio
async def test_a_shown_pdf_is_sent_inline_as_a_pdf(tmp_path: Path) -> None:
    path = tmp_path / f"{SHA}.pdf"
    path.write_bytes(PDF)

    response = await get(Files(StoredFile(file_type="pdf", path=path)))

    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'inline; filename="{SHA}.pdf"'


@pytest.mark.anyio
async def test_a_document_not_shown_is_404() -> None:
    files = Files(None)

    response = await get(files)

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_FOUND}
    assert files.asked == [SHA]


@pytest.mark.anyio
async def test_a_word_file_is_404_with_a_detail_that_says_so(tmp_path: Path) -> None:
    path = tmp_path / f"{SHA}.docx"
    path.write_bytes(b"PK")

    response = await get(Files(StoredFile(file_type="docx", path=path)))

    assert response.status_code == 404
    assert response.json() == {"detail": WORD_FILE}


@pytest.mark.anyio
async def test_a_file_missing_on_disk_is_404_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    response = await get(Files(StoredFile(file_type="pdf", path=tmp_path / "gone.pdf")))

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_ON_DISK}
    assert "not on disk" in caplog.text


@pytest.mark.anyio
async def test_a_database_error_is_503_with_a_fixed_text(caplog: pytest.LogCaptureFixture) -> None:
    error = OperationalError("SELECT ...", {}, Exception("server closed the connection"))

    response = await get(Files(error=error))

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_DOWN}
    assert "server closed the connection" in caplog.text  # the details go to the log


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sha256", "status"),
    [
        ("c3" * 31, 422),  # too short
        ("C3" * 32, 422),  # upper case
        ("g" * 64, 422),  # not hexadecimal
        ("..%2F..%2Fetc%2Fpasswd", 404),  # another path: no route
    ],
)
async def test_a_malformed_hash_is_refused_before_any_lookup(sha256: str, status: int) -> None:
    files = Files(None)

    response = await get(files, sha256)

    assert response.status_code == status
    assert files.asked == []
