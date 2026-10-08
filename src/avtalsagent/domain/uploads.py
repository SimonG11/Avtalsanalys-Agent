"""A file the user uploaded in a conversation, and its sections.

What:
    `UploadKind` ("pdf", "docx", "text"); `UploadSection`, one part of an
    uploaded file as the agent reads and cites it; `Upload`, what is known
    about a stored file without its bytes or text; `NewUpload`, a parsed
    file on its way into the store; and `parse_upload_id` for an id as the
    model or a URL gives it.

Why:
    The API stores the files (`api/uploads.py`) and the agent reads them
    with its own tools. Both use these types, and neither depends on the
    other or on how the store keeps the files (`uploads/store.py`). An
    uploaded file is cut into sections the way the agreements are (by their
    numbered headings, `ingestion/step3_chunk.py`), so the agent reads and
    cites "punkt 6.2" of the user's file as it does an agreement's.

How:
    Frozen Pydantic models, so the API returns them as JSON and a reader
    cannot change what the store gave it. `NewUpload` is a dataclass: it
    carries the file's bytes, which are left out of its repr.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UploadKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"  # .txt and .md, read as UTF-8


class UploadSection(BaseModel):
    """A part of an uploaded file: a numbered section, or a piece of a long one."""

    model_config = ConfigDict(frozen=True)

    position: int  # 0-based order in the file; what the agent names a section by
    number: str | None  # "6.2"; None for text before the first heading or an unnumbered one
    title: str  # the heading without its number; "(del 2 av 3)" added to a long one's pieces
    level: int  # 1 for "6", 2 for "6.2"; 0 for the text before the first heading
    page_start: int | None  # 1-based PDF page the section starts on; None for Word and text
    text: str  # the heading line and the body, exactly as the citation check reads it


class Upload(BaseModel):
    """A stored upload: who it belongs to and what it holds, without its bytes or text."""

    model_config = ConfigDict(frozen=True)

    upload_id: UUID
    thread_id: str  # the AG-UI thread it belongs to; no other thread reaches it
    filename: str  # as uploaded, without path parts or control characters
    kind: UploadKind
    sha256: str  # of the file's bytes; one upload per (thread_id, sha256)
    size: int  # bytes
    pages: int | None  # PDF pages; None for Word and text
    sections: int  # how many `UploadSection`s it has, positions 0 to sections - 1
    characters: int  # of text in all sections
    warnings: tuple[str, ...]  # in Swedish, for the user, e.g. pages without text
    created_at: datetime


@dataclass(frozen=True)
class NewUpload:
    """A parsed file for the store: the upload's fields, its bytes and its sections."""

    thread_id: str
    filename: str
    kind: UploadKind
    sha256: str
    content: bytes = field(repr=False)
    pages: int | None
    characters: int
    warnings: tuple[str, ...]
    sections: tuple[UploadSection, ...] = field(repr=False)


def parse_upload_id(text: str) -> UUID | None:
    """`text` as an upload id, or None when it is not one (an id no upload can have)."""
    try:
        return UUID(text.strip())
    except ValueError:
        return None
