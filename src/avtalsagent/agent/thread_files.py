"""The conversation's own files as the agent reads them: its thread, and a section as a result.

What:
    `thread_of(config)` and `current_thread()`: the AG-UI thread (the
    conversation) a run belongs to. `UploadSectionResult` and
    `section_result(upload, section)`: a section of an uploaded file as the
    agent's tools give it. `require_thread`, `find_upload` and `stored` give
    the thread, the thread's upload by the id the model gave and a store
    call's result, or raise the `ToolException` the model reads
    (`STORE_DOWN` when the store does not answer).

Why:
    A file belongs to the conversation it was uploaded in (ADR 0026), so
    the thread always comes from the run's config, which the API and the
    command line set, and never from the model's arguments: an upload id
    the model copies from another conversation, or makes up, is simply not
    found. A section of an uploaded file has the field names of avtal-mcp's
    `read_section` where they mean the same (sha256, section_position,
    section_number, section_title, page_start, text), so the model cites
    the user's file exactly as it cites an agreement, and the citation
    check reads it again by the same two fields (`upload_readers.py`).

How:
    LangGraph puts `thread_id` in the run's `configurable`; a tool gets the
    config as an argument, the middleware and the readers ask LangGraph for
    the running config (`get_config`), which is None outside a run. The
    sha256 of an upload's section is the hash of the uploaded file's bytes.
"""

import logging
from collections.abc import Awaitable
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import ToolException
from langgraph.config import get_config
from pydantic import BaseModel

from avtalsagent.domain.uploads import Upload, UploadSection, parse_upload_id
from avtalsagent.uploads.store import UploadStore, UploadStoreUnavailable

_log = logging.getLogger(__name__)

STORE_DOWN = (
    "Användarens filer går inte att läsa just nu, eftersom databasen inte svarar. Säg det i "
    "svaret, och svara så långt det går utan filen."
)
NO_THREAD = "Det finns ingen konversation att läsa filer i."
NOT_FOUND = (
    "Det finns ingen fil med upload_id {upload_id} i den här konversationen. Se filerna och "
    "deras upload_id med list_uploads."
)


class UploadSectionResult(BaseModel):
    """A section of the user's file, with the citation fields of avtal-mcp's `read_section`."""

    source: Literal["upload"] = (
        "upload"  # the user's own file, not one of the agreements' documents
    )
    upload_id: str
    filename: str
    sha256: str  # of the uploaded file: copy it to the citation
    section_position: int  # copy it to the citation
    section_number: str | None
    section_title: str
    page_start: int | None  # 1-based PDF page; None for Word and text
    text: str  # exactly as stored: quotes are checked against it


def thread_of(config: RunnableConfig | None) -> str | None:
    """The run's thread (the conversation), or None when the run has none."""
    thread = ((config or {}).get("configurable") or {}).get("thread_id")
    return None if thread is None else str(thread)


def current_thread() -> str | None:
    """The thread of the run this is called in; None outside a run or without a thread."""
    try:
        return thread_of(get_config())
    except RuntimeError:  # not inside a runnable
        return None


def section_result(upload: Upload, section: UploadSection) -> UploadSectionResult:
    """The section as the tools give it, with its file's id, name and hash."""
    return UploadSectionResult(
        upload_id=str(upload.upload_id),
        filename=upload.filename,
        sha256=upload.sha256,
        section_position=section.position,
        section_number=section.number,
        section_title=section.title,
        page_start=section.page_start,
        text=section.text,
    )


def require_thread(config: RunnableConfig | None) -> str:
    """The run's thread, or the `ToolException` the model reads."""
    thread = thread_of(config)
    if thread is None:
        raise ToolException(NO_THREAD)
    return thread


async def find_upload(store: UploadStore, thread: str, upload_id: str) -> Upload:
    """The thread's upload with the model's `upload_id`, or the `ToolException` saying why not.

    An id that is no UUID, another thread's upload and an expired one are
    all "not found": the model learns nothing about other conversations.
    """
    parsed = parse_upload_id(upload_id)
    upload = None if parsed is None else await stored(store.get_upload(thread, parsed))
    if upload is None:
        raise ToolException(NOT_FOUND.format(upload_id=upload_id.strip()[:80]))
    return upload


async def stored[T](call: Awaitable[T]) -> T:
    """`call`'s result, or the `ToolException` the model reads when the store does not answer.

    The store's own error names its database: it is logged, not shown.
    """
    try:
        return await call
    except UploadStoreUnavailable as error:
        _log.warning("the upload store did not answer: %s", error)
        raise ToolException(STORE_DOWN) from None
