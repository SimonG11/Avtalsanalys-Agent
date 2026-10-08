"""`/api/uploads`: the user's own files, which the agent compares with the agreements.

What:
    `router` with `POST /api/uploads` (a multipart form with `file` and
    `thread_id`), `GET /api/uploads?thread_id=` (the thread's files),
    `DELETE /api/uploads/{upload_id}?thread_id=` and
    `GET /api/uploads/{upload_id}/file?thread_id=` (the file as uploaded,
    for the web app's source panel); and `UploadInfo`, an upload as the
    routes give it.

Why:
    The web app attaches a file to the conversation and forwards these
    routes from its own server, as it does `POST /agui` (webbapp-kontrakt.md,
    points 33-38). A file belongs to its thread: every route takes the
    thread's id, and an upload of another thread is 404, as one that does
    not exist, so an upload id alone gives nothing. The upload is checked
    and read before it is stored (`uploads/parse.py`), with every limit
    against large or hostile files, and the answer says what was read: the
    pages, the sections and warnings such as pages without text. The same
    file uploaded again in the thread gives the stored upload (200, not
    201) instead of a copy.

How:
    The upload route reads the form itself: the body is counted as it
    arrives and stopped at UPLOAD_MAX_BYTES and a margin for the form
    (413), and a Content-Length above that is refused before anything is
    read, so a large body never reaches the disk. Then, in order: expired
    uploads are deleted, the same file in the thread is returned at once,
    a thread with UPLOAD_MAX_PER_THREAD files is 409, and the file is read
    in a child process (`app.state.parser`, `uploads/parse_process.py`),
    which is killed after PARSE_SECONDS (422) or when it passes its memory
    limit, so a hostile file can neither hold the API's threads nor bring
    its process down. The store then adds it, checking the thread's limit,
    the same file again and the space all files may take (507 when they
    take it all) in one transaction.
    A store whose database does not answer is 503 with a fixed text, the
    details logged. The file route sends a PDF and text inline and a Word
    file as an attachment, with `nosniff` and the stored name, sanitized
    again, in both an ASCII and a UTF-8 form.
"""

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message

from avtalsagent.api.documents import DATABASE_DOWN
from avtalsagent.config import Settings
from avtalsagent.domain.uploads import NewUpload, Upload, UploadKind
from avtalsagent.uploads.errors import TOO_LARGE, UploadRejected
from avtalsagent.uploads.file_type import safe_filename
from avtalsagent.uploads.parse import ParsedFile, UploadLimits, megabytes
from avtalsagent.uploads.parse_process import ProcessParser
from avtalsagent.uploads.store import (
    StoreFull,
    TooManyUploads,
    UploadStore,
    UploadStoreUnavailable,
)

_log = logging.getLogger(__name__)

# Bytes of the form around the file: the part headers and the thread's id.
FORM_MARGIN = 64 * 1024
THREAD_ID_MAX_CHARS = 200

NOT_FOUND = "Filen finns inte i den här konversationen."
TOO_MANY = "Konversationen har redan {limit} filer. Ta bort en innan du laddar upp en ny."
STORE_FULL = "Det finns inte plats för fler filer just nu. Försök igen senare."
BAD_FORM = "Uppladdningen ska vara ett formulär med fälten file och thread_id."

MEDIA_TYPES = {
    UploadKind.PDF: "application/pdf",
    UploadKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    UploadKind.TEXT: "text/plain; charset=utf-8",
}

router = APIRouter()

ThreadId = Annotated[
    str, Query(min_length=1, max_length=THREAD_ID_MAX_CHARS, description="AG-UI-trådens id.")
]


class UploadInfo(BaseModel):
    """An upload as the routes give it (webbapp-kontrakt.md, point 33)."""

    upload_id: UUID
    filename: str
    kind: UploadKind
    pages: int | None
    sections: int
    characters: int
    size: int
    warnings: list[str]
    created_at: datetime

    @classmethod
    def of(cls, upload: Upload) -> "UploadInfo":
        return cls(
            upload_id=upload.upload_id,
            filename=upload.filename,
            kind=upload.kind,
            pages=upload.pages,
            sections=upload.sections,
            characters=upload.characters,
            size=upload.size,
            warnings=list(upload.warnings),
            created_at=upload.created_at,
        )


class UploadList(BaseModel):
    uploads: list[UploadInfo]


class _BodyTooLarge(Exception):
    """The request's body passed the limit while it was read."""


def _store(request: Request) -> UploadStore:
    store: UploadStore = request.app.state.uploads
    return store


def _parser(request: Request) -> ProcessParser:
    parser: ProcessParser = request.app.state.parser
    return parser


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def _stored[T](call: Callable[[], Awaitable[T]]) -> T:
    """The store's answer; 503 with the fixed text when its database does not answer."""
    try:
        return await call()
    except UploadStoreUnavailable:
        _log.exception("the upload store did not answer")
        raise HTTPException(status_code=503, detail=DATABASE_DOWN) from None


_FORM_SCHEMA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "thread_id"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "thread_id": {"type": "string", "maxLength": THREAD_ID_MAX_CHARS},
                    },
                }
            }
        },
    }
}


@router.post(
    "/api/uploads",
    status_code=201,
    response_model=UploadInfo,
    openapi_extra=_FORM_SCHEMA,
    responses={
        200: {"model": UploadInfo, "description": "Samma fil fanns redan i tråden."},
        409: {"description": "Tråden har redan så många filer som den får ha."},
        413: {"description": "För stor fil, för många sidor eller för mycket text."},
        415: {"description": "Filtypen stöds inte."},
        422: {
            "description": "Ingen text att läsa, en fil som inte går att läsa eller som tar "
            "för lång tid att läsa."
        },
        503: {"description": "Databasen svarar inte."},
        507: {"description": "Filerna tar redan all plats de får ta."},
    },
)
async def upload_file(request: Request, response: Response) -> UploadInfo:
    """Store a file for the thread, read into sections (see the module)."""
    settings = _settings(request)
    store = _store(request)
    data, filename, thread_id = await _read_form(request, settings.upload_max_bytes)
    await _stored(store.delete_expired)
    sha256 = hashlib.sha256(data).hexdigest()
    current = await _stored(lambda: store.list_uploads(thread_id))
    if same := next((upload for upload in current if upload.sha256 == sha256), None):
        response.status_code = 200
        return UploadInfo.of(same)
    if len(current) >= settings.upload_max_per_thread:
        raise HTTPException(409, detail=TOO_MANY.format(limit=settings.upload_max_per_thread))
    parsed = await _parse(_parser(request), data, filename, UploadLimits.from_settings(settings))
    new = NewUpload(
        thread_id=thread_id,
        filename=filename,
        kind=parsed.kind,
        sha256=sha256,
        content=data,
        pages=parsed.pages,
        characters=parsed.characters,
        warnings=parsed.warnings,
        sections=parsed.sections,
    )
    try:
        upload, created = await _stored(
            lambda: store.add_upload(
                new,
                settings.upload_max_per_thread,
                max_total_bytes=settings.upload_max_total_bytes,
            )
        )
    except TooManyUploads:
        raise HTTPException(
            409, detail=TOO_MANY.format(limit=settings.upload_max_per_thread)
        ) from None
    except StoreFull:
        _log.warning("the uploads take UPLOAD_MAX_TOTAL_BYTES; a file was refused")
        raise HTTPException(507, detail=STORE_FULL) from None
    response.status_code = 201 if created else 200
    return UploadInfo.of(upload)


async def _read_form(request: Request, max_bytes: int) -> tuple[bytes, str, str]:
    """The file's bytes, its sanitized name and the thread's id; 413 past the limit."""
    too_large = HTTPException(413, detail=TOO_LARGE.format(limit=megabytes(max_bytes)))
    limit = max_bytes + FORM_MARGIN
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _BodyTooLarge
        return message

    counted = Request(request.scope, receive)
    try:
        async with counted.form(max_files=1, max_fields=4, max_part_size=1024) as form:
            file = form.get("file")
            thread_id = form.get("thread_id")
            if not isinstance(file, UploadFile) or not isinstance(thread_id, str):
                raise HTTPException(422, detail=BAD_FORM)
            thread_id = thread_id.strip()
            if not 0 < len(thread_id) <= THREAD_ID_MAX_CHARS:
                raise HTTPException(422, detail=BAD_FORM)
            data = await file.read(max_bytes + 1)
            filename = safe_filename(file.filename or "")
    except _BodyTooLarge:
        raise too_large from None
    except StarletteHTTPException as error:
        if error.status_code == 400:  # Starlette's, in English, for a form it cannot parse
            raise HTTPException(422, detail=BAD_FORM) from None
        raise
    if len(data) > max_bytes:
        raise too_large
    return data, filename, thread_id


async def _parse(
    parser: ProcessParser, data: bytes, filename: str, limits: UploadLimits
) -> ParsedFile:
    """The file read in a child process; a refusal as an HTTP error."""
    try:
        return await parser.parse(data, filename, limits)
    except UploadRejected as rejected:
        raise HTTPException(rejected.status_code, detail=rejected.detail) from None


@router.get(
    "/api/uploads",
    response_model=UploadList,
    responses={503: {"description": "Databasen svarar inte."}},
)
async def list_files(thread_id: ThreadId, request: Request) -> UploadList:
    """The thread's uploads, oldest first."""
    store = _store(request)
    uploads = await _stored(lambda: store.list_uploads(thread_id))
    return UploadList(uploads=[UploadInfo.of(upload) for upload in uploads])


@router.delete(
    "/api/uploads/{upload_id}",
    status_code=204,
    responses={
        404: {"description": "Tråden har ingen sådan fil."},
        503: {"description": "Databasen svarar inte."},
    },
)
async def delete_file(upload_id: UUID, thread_id: ThreadId, request: Request) -> Response:
    """Delete one of the thread's uploads."""
    store = _store(request)
    if not await _stored(lambda: store.delete_upload(thread_id, upload_id)):
        raise HTTPException(404, detail=NOT_FOUND)
    return Response(status_code=204)


@router.get(
    "/api/uploads/{upload_id}/file",
    response_class=Response,
    responses={
        200: {
            "content": {media_type: {} for media_type in MEDIA_TYPES.values()},
            "description": "Filen som den laddades upp.",
        },
        404: {"description": "Tråden har ingen sådan fil."},
        503: {"description": "Databasen svarar inte."},
    },
)
async def file_content(upload_id: UUID, thread_id: ThreadId, request: Request) -> Response:
    """The file as uploaded: a PDF or text inline, a Word file as an attachment."""
    store = _store(request)
    upload = await _stored(lambda: store.get_upload(thread_id, upload_id))
    content = (
        None if upload is None else await _stored(lambda: store.read_content(thread_id, upload_id))
    )
    if upload is None or content is None:
        raise HTTPException(404, detail=NOT_FOUND)
    disposition = "attachment" if upload.kind is UploadKind.DOCX else "inline"
    return Response(
        content=content,
        media_type=MEDIA_TYPES[upload.kind],
        headers={
            "Content-Disposition": content_disposition(disposition, upload.filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def content_disposition(disposition: str, filename: str) -> str:
    """The header with the name in ASCII and, when it has other characters, in UTF-8."""
    name = safe_filename(filename)
    ascii_name = "".join(char if char.isascii() and char not in '"\\' else "_" for char in name)
    header = f'{disposition}; filename="{ascii_name}"'
    if ascii_name != name:
        header += f"; filename*=UTF-8''{quote(name, safe='')}"
    return header
