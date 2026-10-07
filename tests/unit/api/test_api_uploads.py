"""Tests for avtalsagent.api.uploads: the user's own files, through the whole app.

The app runs with the memory store (the default) or a stand-in, a scripted
model and no database. Each route is called through httpx: an upload is
read and listed, sent back with its type and a safe name, and deleted; the
same file again is the same upload; another thread gets 404; each limit
and refusal has its status and Swedish text, also for a body without a
Content-Length; a store whose database is down is 503; a file that takes
too long to read is 422; expired uploads are deleted on the next upload.
"""

import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.api import uploads as upload_routes
from avtalsagent.api.app import create_app
from avtalsagent.api.documents import DATABASE_DOWN, DocumentFiles, StoredFile
from avtalsagent.api.uploads import BAD_FORM, NOT_FOUND, TOO_SLOW
from avtalsagent.config import Settings
from avtalsagent.domain.uploads import NewUpload, Upload, UploadSection
from avtalsagent.uploads.errors import SCANNED, UNSUPPORTED
from avtalsagent.uploads.store import MemoryUploadStore, UploadStore, UploadStoreUnavailable
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
)
from tests.unit.uploads.upload_files import AGREEMENT_PAGES, docx_bytes, pdf_bytes

THREAD = "f3b2c1d0-thread"
OTHER = "another-thread"
PDF = pdf_bytes(AGREEMENT_PAGES)
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class NoFiles:
    def find(self, sha256: str) -> StoredFile | None:
        return None


def app_with(
    settings: Settings | None = None, store: UploadStore | None = None, **more: Any
) -> FastAPI:
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
        yield NoFiles()

    options: dict[str, Any] = {}
    if store is not None:

        @asynccontextmanager
        async def open_uploads(settings: Settings) -> AsyncIterator[UploadStore]:
            yield store

        options["open_uploads"] = open_uploads
    return create_app(
        settings or Settings(_env_file=None),
        make_model=lambda settings: ScriptedModel(script=[]),
        make_answer_reviewer=lambda settings: ScriptedReviewer(),
        open_tools=open_tools,
        open_saver=open_saver,
        open_documents=open_documents,
        **options,
        **more,
    )


@asynccontextmanager
async def client_of(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def upload(
    client: httpx.AsyncClient, data: bytes, filename: str = "avtal.pdf", thread: str = THREAD
) -> httpx.Response:
    return await client.post(
        "/api/uploads", data={"thread_id": thread}, files={"file": (filename, data)}
    )


@pytest.mark.anyio
async def test_an_upload_is_read_listed_sent_back_and_deleted() -> None:
    async with client_of(app_with()) as client:
        created = await upload(client, PDF)
        assert created.status_code == 201
        body = created.json()
        upload_id = body["upload_id"]
        UUID(upload_id)
        assert {k: v for k, v in body.items() if k not in ("upload_id", "created_at")} == {
            "filename": "avtal.pdf",
            "kind": "pdf",
            "pages": 4,
            "sections": 5,
            "characters": body["characters"],
            "size": len(PDF),
            "warnings": [
                "Sidan 3 har ingen text som går att läsa (den kan vara inskannad). "
                "Agenten ser inte vad som står på den."
            ],
        }
        assert body["characters"] > 100

        listed = await client.get("/api/uploads", params={"thread_id": THREAD})
        assert listed.status_code == 200
        assert listed.json() == {"uploads": [body]}

        sent = await client.get(f"/api/uploads/{upload_id}/file", params={"thread_id": THREAD})
        assert sent.status_code == 200
        assert sent.content == PDF
        assert sent.headers["content-type"] == "application/pdf"
        assert sent.headers["content-disposition"] == 'inline; filename="avtal.pdf"'
        assert sent.headers["x-content-type-options"] == "nosniff"

        deleted = await client.delete(f"/api/uploads/{upload_id}", params={"thread_id": THREAD})
        assert deleted.status_code == 204
        gone = await client.get(f"/api/uploads/{upload_id}/file", params={"thread_id": THREAD})
        assert (gone.status_code, gone.json()) == (404, {"detail": NOT_FOUND})
        again = await client.delete(f"/api/uploads/{upload_id}", params={"thread_id": THREAD})
        assert again.status_code == 404
        listed = await client.get("/api/uploads", params={"thread_id": THREAD})
        assert listed.json() == {"uploads": []}


@pytest.mark.anyio
async def test_the_same_file_again_is_the_same_upload_with_200() -> None:
    async with client_of(app_with()) as client:
        first = await upload(client, PDF)
        second = await upload(client, PDF, filename="kopia.pdf")

        assert (first.status_code, second.status_code) == (201, 200)
        assert second.json() == first.json()
        listed = await client.get("/api/uploads", params={"thread_id": THREAD})
        assert len(listed.json()["uploads"]) == 1


@pytest.mark.anyio
async def test_another_thread_gets_404_and_sees_nothing() -> None:
    async with client_of(app_with()) as client:
        upload_id = (await upload(client, PDF)).json()["upload_id"]

        sent = await client.get(f"/api/uploads/{upload_id}/file", params={"thread_id": OTHER})
        deleted = await client.delete(f"/api/uploads/{upload_id}", params={"thread_id": OTHER})
        listed = await client.get("/api/uploads", params={"thread_id": OTHER})
        unknown = await client.get(f"/api/uploads/{uuid4()}/file", params={"thread_id": THREAD})

        assert (sent.status_code, sent.json()) == (404, {"detail": NOT_FOUND})
        assert deleted.status_code == 404
        assert listed.json() == {"uploads": []}
        assert unknown.status_code == 404
        mine = await client.get(f"/api/uploads/{upload_id}/file", params={"thread_id": THREAD})
        assert mine.status_code == 200  # the other thread's delete did nothing


@pytest.mark.anyio
async def test_word_is_an_attachment_and_text_inline_as_utf8() -> None:
    word = docx_bytes(lambda document: document.add_paragraph("Ett avtal."))
    async with client_of(app_with()) as client:
        word_id = (await upload(client, word, "villkor.docx")).json()["upload_id"]
        text_id = (await upload(client, "Sjö och å.".encode(), "noter.md")).json()["upload_id"]

        sent_word = await client.get(f"/api/uploads/{word_id}/file", params={"thread_id": THREAD})
        sent_text = await client.get(f"/api/uploads/{text_id}/file", params={"thread_id": THREAD})

    assert sent_word.content == word
    assert sent_word.headers["content-type"] == DOCX_TYPE
    assert sent_word.headers["content-disposition"] == 'attachment; filename="villkor.docx"'
    assert sent_text.text == "Sjö och å."
    assert sent_text.headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.anyio
async def test_a_name_with_a_path_and_other_characters_is_sanitized() -> None:
    # httpx would percent-encode the name, as browsers do; a client need not.
    body = (
        (
            f'--b\r\nContent-Disposition: form-data; name="thread_id"\r\n\r\n{THREAD}\r\n'
            '--b\r\nContent-Disposition: form-data; name="file"; '
            'filename="../../Avtal \\"å\\"\t.pdf"\r\n\r\n'
        ).encode()
        + PDF
        + b"\r\n--b--\r\n"
    )
    async with client_of(app_with()) as client:
        created = await client.post(
            "/api/uploads",
            content=body,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )
        upload_id = created.json()["upload_id"]
        sent = await client.get(f"/api/uploads/{upload_id}/file", params={"thread_id": THREAD})

    assert created.json()["filename"] == 'Avtal "å".pdf'
    assert sent.headers["content-disposition"] == (
        "inline; filename=\"Avtal ___.pdf\"; filename*=UTF-8''Avtal%20%22%C3%A5%22.pdf"
    )


@pytest.mark.anyio
async def test_a_file_over_the_limit_is_413_whether_or_not_the_length_is_given() -> None:
    settings = Settings(_env_file=None, upload_max_bytes=1024 * 1024)
    big = b"a" * (1024 * 1024 + 1)
    boundary = "grans"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="thread_id"\r\n\r\n{THREAD}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + b"a" * (2 * 1024 * 1024)
        + f"\r\n--{boundary}--\r\n".encode()
    )

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 64 * 1024):
            yield body[start : start + 64 * 1024]

    async with client_of(app_with(settings)) as client:
        just_over = await upload(client, big, "a.txt")
        streamed = await client.post(
            "/api/uploads",
            content=chunks(),
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        declared = await client.post(
            "/api/uploads",
            content=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )

    detail = {"detail": "Filen är för stor. Den får vara högst 1 MB."}
    assert "content-length" not in streamed.request.headers
    for response in (just_over, streamed, declared):
        assert (response.status_code, response.json()) == (413, detail)


@pytest.mark.anyio
async def test_another_type_is_415_and_a_scanned_pdf_422() -> None:
    async with client_of(app_with()) as client:
        program = await upload(client, b"MZ\x90\x00", "program.exe")
        scanned = await upload(client, pdf_bytes([[]]), "inskannad.pdf")
        listed = await client.get("/api/uploads", params={"thread_id": THREAD})

    assert (program.status_code, program.json()) == (415, {"detail": UNSUPPORTED})
    assert (scanned.status_code, scanned.json()) == (422, {"detail": SCANNED})
    assert listed.json() == {"uploads": []}


@pytest.mark.anyio
async def test_a_form_without_its_fields_is_422() -> None:
    async with client_of(app_with()) as client:
        no_thread = await client.post("/api/uploads", files={"file": ("a.txt", b"Hej")})
        no_file = await client.post("/api/uploads", data={"thread_id": THREAD})
        blank = await client.post(
            "/api/uploads", data={"thread_id": "  "}, files={"file": ("a.txt", b"Hej")}
        )
        json_body = await client.post("/api/uploads", json={"thread_id": THREAD})
        broken = await client.post(
            "/api/uploads",
            content=b"--x\r\nnot a part",
            headers={"content-type": "multipart/form-data; boundary=x"},
        )

    for response in (no_thread, no_file, blank, json_body, broken):
        assert (response.status_code, response.json()) == (422, {"detail": BAD_FORM})


@pytest.mark.anyio
async def test_a_thread_with_max_files_is_409_until_one_is_deleted() -> None:
    settings = Settings(_env_file=None, upload_max_per_thread=1)
    async with client_of(app_with(settings)) as client:
        first = (await upload(client, b"Ett", "ett.txt")).json()
        refused = await upload(client, b"Tva", "tva.txt")
        same_again = await upload(client, b"Ett", "ett.txt")
        await client.delete(f"/api/uploads/{first['upload_id']}", params={"thread_id": THREAD})
        after = await upload(client, b"Tva", "tva.txt")
        elsewhere = await upload(client, b"Tre", "tre.txt", thread=OTHER)

    assert refused.status_code == 409
    assert refused.json() == {
        "detail": "Konversationen har redan 1 filer. Ta bort en innan du laddar upp en ny."
    }
    assert same_again.status_code == 200
    assert (after.status_code, elsewhere.status_code) == (201, 201)


class DownStore:
    """A store whose database does not answer."""

    def __getattr__(self, name: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise UploadStoreUnavailable("connection refused")

        return fail


@pytest.mark.anyio
async def test_a_store_that_does_not_answer_is_503_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store: Any = DownStore()
    async with client_of(app_with(store=store)) as client:
        responses = [
            await upload(client, PDF),
            await client.get("/api/uploads", params={"thread_id": THREAD}),
            await client.get(f"/api/uploads/{uuid4()}/file", params={"thread_id": THREAD}),
            await client.delete(f"/api/uploads/{uuid4()}", params={"thread_id": THREAD}),
        ]

    for response in responses:
        assert (response.status_code, response.json()) == (503, {"detail": DATABASE_DOWN})
    assert "connection refused" in caplog.text


@pytest.mark.anyio
async def test_a_file_that_takes_too_long_to_read_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(*args: Any) -> None:
        time.sleep(0.5)

    monkeypatch.setattr(upload_routes, "PARSE_SECONDS", 0.05)
    monkeypatch.setattr(upload_routes, "parse_upload", slow)
    async with client_of(app_with()) as client:
        response = await upload(client, PDF)
        listed = await client.get("/api/uploads", params={"thread_id": THREAD})

    assert (response.status_code, response.json()) == (422, {"detail": TOO_SLOW})
    assert listed.json() == {"uploads": []}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class CountingStore(MemoryUploadStore):
    """The memory store, counting the uploads its `delete_expired` removes."""

    def __init__(self, clock: Clock) -> None:
        super().__init__(timedelta(days=7), clock)
        self.expired = 0

    async def delete_expired(self) -> int:
        deleted = await super().delete_expired()
        self.expired += deleted
        return deleted


@pytest.mark.anyio
async def test_expired_uploads_are_deleted_on_the_next_upload() -> None:
    clock = Clock()
    store = CountingStore(clock)
    async with client_of(app_with(store=store)) as client:
        old = (await upload(client, b"Gammal", "gammal.txt")).json()
        clock.now += timedelta(days=8)
        await upload(client, b"Ny", "ny.txt", thread=OTHER)
        listed = await client.get("/api/uploads", params={"thread_id": THREAD})
        sent = await client.get(
            f"/api/uploads/{old['upload_id']}/file", params={"thread_id": THREAD}
        )

    assert store.expired == 1
    assert listed.json() == {"uploads": []}
    assert sent.status_code == 404


@pytest.mark.anyio
async def test_the_thread_and_the_id_are_checked_before_any_lookup() -> None:
    store: Any = DownStore()  # any lookup would be 503
    async with client_of(app_with(store=store)) as client:
        no_thread = await client.get("/api/uploads")
        long_thread = await client.get("/api/uploads", params={"thread_id": "x" * 201})
        bad_id = await client.get("/api/uploads/not-a-uuid/file", params={"thread_id": THREAD})

    assert [r.status_code for r in (no_thread, long_thread, bad_id)] == [422, 422, 422]


def test_the_upload_route_documents_its_form() -> None:
    schema = app_with().openapi()["paths"]["/api/uploads"]["post"]

    form = schema["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert form["required"] == ["file", "thread_id"]
    assert set(schema["responses"]) >= {"200", "201", "409", "413", "415", "422", "503"}


class RecordingStore(MemoryUploadStore):
    """The memory store, recording what is added."""

    def __init__(self) -> None:
        super().__init__(timedelta(days=7))
        self.added: list[NewUpload] = []

    async def add_upload(self, upload: NewUpload, max_per_thread: int) -> tuple[Upload, bool]:
        self.added.append(upload)
        return await super().add_upload(upload, max_per_thread)


@pytest.mark.anyio
async def test_the_store_gets_the_file_its_hash_and_its_sections() -> None:
    store = RecordingStore()
    async with client_of(app_with(store=store)) as client:
        upload_id = UUID((await upload(client, PDF)).json()["upload_id"])

    (added,) = store.added
    assert (added.thread_id, added.filename, added.content) == (THREAD, "avtal.pdf", PDF)
    assert len(added.sha256) == 64
    sections = await store.read_sections(THREAD, upload_id)
    assert sections == list(added.sections)
    assert sections[4] == UploadSection(
        position=4,
        number="3",
        title="Uppsägning",
        level=1,
        page_start=4,
        text="3 Uppsägning\nUppsägningstiden är tre (3) månader.",
    )
