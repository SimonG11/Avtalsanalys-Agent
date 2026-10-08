"""The answer check's readers for a conversation with files: the user's file first, then avtal-mcp.

What:
    `UploadSectionReader(store, fallback)`, a `SectionReader`: a cited
    (sha256, section_position) that is a file of the conversation is read
    from the store, as a `CitedSection` with source "upload"; anything else
    from `fallback` (avtal-mcp). `UploadAmendmentReader(store, fallback)`,
    an `AmendmentReader`: a section of the conversation's file has no
    amendments; anything else is `fallback`'s.

Why:
    The check reads every cited section again itself, never from the
    message history (ADR 0013), and a quote from the user's file is checked
    like any other: against the text the store keeps, which `read_upload`
    gave the model (ADR 0026). The check finds the file by the conversation
    of the run, so a hash the model copies from another conversation is
    not the user's file here and is not found. An uploaded file has no
    amendments in the agreements, and avtal-mcp does not know its hash:
    asking it would read as "the amendments could not be read" and give
    every answer that cites the file a reservation.

How:
    Both readers take the thread from the running graph's config
    (`thread_files.current_thread`; a test passes `thread`). A read lists
    the thread's uploads (a handful) and looks for the hash; with a match
    the section is the store's (None when it has no such position, without
    asking avtal-mcp), its file name the citation's file title, its PDF
    page the citation's page and no agreement page. A store that does not
    answer is logged and taken as no files: the citation is then checked
    against avtal-mcp, which does not know it, so it fails like any source
    that could not be read. A file the user uploads that is byte for byte
    one of the agreements' documents has the same hash; in its
    conversation the hash is the uploaded file's (a known limit, ADR 0026).
"""

import logging
from collections.abc import Callable

from avtalsagent.agent.amendments import AmendmentInfo, AmendmentReader
from avtalsagent.agent.sections import CitedSection, SectionReader
from avtalsagent.agent.thread_files import current_thread
from avtalsagent.domain.uploads import Upload
from avtalsagent.uploads.store import UploadStore, UploadStoreUnavailable

_log = logging.getLogger(__name__)


async def thread_upload(
    store: UploadStore, thread: Callable[[], str | None], sha256: str
) -> Upload | None:
    """The conversation's upload whose file has `sha256`, or None."""
    current = thread()
    if current is None:
        return None
    try:
        uploads = await store.list_uploads(current)
    except UploadStoreUnavailable as error:
        _log.warning(
            "the upload store did not answer; %s… is read from avtal-mcp: %s", sha256[:12], error
        )
        return None
    return next((upload for upload in uploads if upload.sha256 == sha256), None)


class UploadSectionReader:
    """A `SectionReader` over the conversation's files, then `fallback`."""

    def __init__(
        self,
        store: UploadStore,
        fallback: SectionReader,
        thread: Callable[[], str | None] = current_thread,
    ) -> None:
        self._store = store
        self._fallback = fallback
        self._thread = thread

    async def read(self, sha256: str, section_position: int) -> CitedSection | None:
        """The section of the conversation's file with `sha256`, else `fallback`'s."""
        upload = await thread_upload(self._store, self._thread, sha256)
        if upload is None:
            return await self._fallback.read(sha256, section_position)
        try:
            section = await self._store.read_section(
                upload.thread_id, upload.upload_id, section_position
            )
        except UploadStoreUnavailable as error:
            _log.warning("the upload store did not answer reading a cited section: %s", error)
            return None
        if section is None:
            return None
        return CitedSection(
            sha256=upload.sha256,
            section_position=section.position,
            section_number=section.number,
            section_title=section.title,
            file_title=upload.filename,
            page_titles=[],
            page_start=section.page_start,
            text=section.text,
            source="upload",
            upload_id=str(upload.upload_id),
        )


class UploadAmendmentReader:
    """An `AmendmentReader` that gives no amendments for the conversation's files."""

    def __init__(
        self,
        store: UploadStore,
        fallback: AmendmentReader,
        thread: Callable[[], str | None] = current_thread,
    ) -> None:
        self._store = store
        self._fallback = fallback
        self._thread = thread

    async def read(self, sha256: str, section_position: int) -> list[AmendmentInfo] | None:
        """None of the agreements' amendments change the user's file; else `fallback`'s."""
        if await thread_upload(self._store, self._thread, sha256) is not None:
            return []
        return await self._fallback.read(sha256, section_position)
