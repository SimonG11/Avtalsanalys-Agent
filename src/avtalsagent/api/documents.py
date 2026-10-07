"""`GET /api/documents/{sha256}/pdf`: the PDF that an answer's citation points to.

What:
    `router` with the route; `DocumentFiles`, which finds a document's
    stored file when the agent may show it; and `DatabaseDocumentFiles`,
    which does so from the database and the data directory.

Why:
    The web app shows the cited page in a PDF viewer next to the answer,
    through its own route that forwards here (webbapp-kontrakt.md). The API
    serves only what the agent may cite: a file avtal-mcp shows, by the
    same rule (`mcp_server.visibility`: indexed and not held back; ADR 0011
    and 0012). A file the ingestion holds back can then not be fetched by
    its hash either. A Word file has no pages: its citations have no page
    and the web app shows the quote without the viewer (clarification 15),
    and asked for anyway it is 404 with a detail that says why.

How:
    The hash must be 64 lowercase hexadecimal digits (FastAPI answers 422
    otherwise, before any lookup). The route is a plain function, which
    FastAPI runs in a worker thread, so the synchronous database query does
    not hold up the agent's runs. The lookup reads, on one read-only
    session, what the tools may show (`load_visibility`) and the file's row
    in `source_document`: its type and its path under DATA_DIR, where step
    1 stored it under its hash. The PDF is sent inline (`FileResponse`,
    `application/pdf`). A database error is 503 with a fixed text, the
    details logged; a file the database lists but the disk lacks is 404,
    and logged, since it means the data directory and the database differ.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.mcp_server.visibility import load_visibility

_log = logging.getLogger(__name__)

SHA256_PATTERN = "^[0-9a-f]{64}$"

NOT_FOUND = "Dokumentet finns inte, eller visas inte eftersom inläsningen håller tillbaka det."
WORD_FILE = "Dokumentet är en Word-fil och har ingen PDF; citatet står i svaret utan sidvisning."
NOT_ON_DISK = "Dokumentets fil finns inte på servern."
DATABASE_DOWN = "Databasen kunde inte svara just nu. Försök igen om en stund."

router = APIRouter()


@dataclass(frozen=True)
class StoredFile:
    """A document's file as step 1 stored it: its type ("pdf", "docx") and where it is."""

    file_type: str
    path: Path


class DocumentFiles(Protocol):
    def find(self, sha256: str) -> StoredFile | None:
        """The document's file, or None when there is none or the agent may not show it."""
        ...


class DatabaseDocumentFiles:
    """`DocumentFiles` from the database: what avtal-mcp shows, stored under `data_dir`."""

    def __init__(self, sessions: Callable[[], Session], data_dir: Path) -> None:
        self._sessions = sessions
        self._data_dir = data_dir

    def find(self, sha256: str) -> StoredFile | None:
        with self._sessions() as session:
            if not load_visibility(session).shows_file(sha256):
                return None
            # A file linked from several addresses has a row for each; they name one file.
            row = session.execute(
                select(models.SourceDocument.file_type, models.SourceDocument.local_path)
                .where(models.SourceDocument.sha256 == sha256)
                .order_by(models.SourceDocument.url)
                .limit(1)
            ).first()
        if row is None:
            return None
        file_type, local_path = row
        return StoredFile(file_type=file_type, path=self._data_dir / local_path)


@router.get(
    "/api/documents/{sha256}/pdf",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Dokumentets PDF."},
        404: {"description": "Inget sådant dokument som visas, eller en Word-fil."},
        503: {"description": "Databasen svarar inte."},
    },
)
def document_pdf(
    sha256: Annotated[str, PathParameter(pattern=SHA256_PATTERN)], request: Request
) -> FileResponse:
    """The document's PDF, when the agent may show the document."""
    files: DocumentFiles = request.app.state.documents
    try:
        stored = files.find(sha256)
    except SQLAlchemyError:
        _log.exception("could not look up the document %s", sha256)
        raise HTTPException(status_code=503, detail=DATABASE_DOWN) from None
    if stored is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if stored.file_type != "pdf":
        raise HTTPException(status_code=404, detail=WORD_FILE)
    if not stored.path.is_file():
        _log.warning("the database lists %s at %s, which is not on disk", sha256, stored.path)
        raise HTTPException(status_code=404, detail=NOT_ON_DISK)
    return FileResponse(
        stored.path,
        media_type="application/pdf",
        filename=f"{sha256}.pdf",
        content_disposition_type="inline",
    )
