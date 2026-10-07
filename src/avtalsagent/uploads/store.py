"""Where the user's files are kept: per conversation, in memory or in Postgres.

What:
    `UploadStore`, the protocol the API writes through and the agent reads
    through; `MemoryUploadStore`; `open_upload_store(settings)`, which
    yields the store UPLOAD_STORE names ("memory", or "postgres":
    `postgres_store.PostgresUploadStore`) with the expired files deleted;
    and the store's errors, `TooManyUploads` and `UploadStoreUnavailable`.

Why:
    A file belongs to the conversation it was uploaded in (the AG-UI
    thread): every read takes the thread's id with the upload's, and an
    upload of another thread is not found, exactly as one that does not
    exist, so an id that leaks gives nothing. The same file uploaded again
    in a thread is the same upload, not a second copy. Files are kept for
    UPLOAD_RETENTION_DAYS and are not read after that, even before they are
    deleted. As the checkpointer, the store is in memory for the tests, the
    command line and a run without a database, and in Postgres in the API's
    container (ADR 0004: one database), where a conversation survives a
    restart.

How:
    The reads, for the routes and the agent's tools (all `async`; None or
    an empty list when the thread has no such upload):
    - `list_uploads(thread_id)`: the thread's uploads, oldest first.
    - `get_upload(thread_id, upload_id)`: one upload.
    - `read_sections(thread_id, upload_id)`: all its sections, in order.
    - `read_section(thread_id, upload_id, position)`: one section.
    - `read_content(thread_id, upload_id)`: the file's bytes.
    The writes: `add_upload(upload, max_per_thread)` gives the stored upload
    and whether it is new (False when the thread has the same file already)
    and raises `TooManyUploads` when the thread has `max_per_thread`;
    `delete_upload` and `delete_expired`. Any of them raises
    `UploadStoreUnavailable` when the database does not answer. The memory
    store keeps everything in dicts, behind one lock for the writes.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from avtalsagent.config import Settings
from avtalsagent.domain.uploads import NewUpload, Upload, UploadSection


class TooManyUploads(Exception):
    """The thread already has as many uploads as it may."""


class UploadStoreUnavailable(Exception):
    """The store's database did not answer; the message is for the log only."""


class UploadStore(Protocol):
    """A conversation's uploaded files (see the module for each method)."""

    async def add_upload(self, upload: NewUpload, max_per_thread: int) -> tuple[Upload, bool]:
        """The stored upload and True, or the thread's upload of the same file and False."""
        ...

    async def list_uploads(self, thread_id: str) -> list[Upload]:
        """The thread's uploads, oldest first."""
        ...

    async def get_upload(self, thread_id: str, upload_id: UUID) -> Upload | None:
        """The upload, or None when the thread has no such upload."""
        ...

    async def read_sections(self, thread_id: str, upload_id: UUID) -> list[UploadSection]:
        """The upload's sections in order; empty when the thread has no such upload."""
        ...

    async def read_section(
        self, thread_id: str, upload_id: UUID, position: int
    ) -> UploadSection | None:
        """One section, or None when the thread has no such upload or section."""
        ...

    async def read_content(self, thread_id: str, upload_id: UUID) -> bytes | None:
        """The file's bytes as uploaded, or None when the thread has no such upload."""
        ...

    async def delete_upload(self, thread_id: str, upload_id: UUID) -> bool:
        """Delete the upload; False when the thread had no such upload."""
        ...

    async def delete_expired(self) -> int:
        """Delete the uploads older than the retention; how many were deleted."""
        ...


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryUploadStore:
    """`UploadStore` in dicts, for as long as the process runs."""

    def __init__(self, retention: timedelta, clock: Callable[[], datetime] = _now) -> None:
        self._retention = retention
        self._clock = clock
        self._uploads: dict[UUID, Upload] = {}
        self._contents: dict[UUID, bytes] = {}
        self._sections: dict[UUID, tuple[UploadSection, ...]] = {}
        self._lock = asyncio.Lock()

    def _find(self, thread_id: str, upload_id: UUID) -> Upload | None:
        upload = self._uploads.get(upload_id)
        if upload is None or upload.thread_id != thread_id or self._expired(upload):
            return None
        return upload

    def _expired(self, upload: Upload) -> bool:
        return upload.created_at < self._clock() - self._retention

    async def add_upload(self, upload: NewUpload, max_per_thread: int) -> tuple[Upload, bool]:
        async with self._lock:
            await self.delete_expired()
            current = await self.list_uploads(upload.thread_id)
            same = next((stored for stored in current if stored.sha256 == upload.sha256), None)
            if same is not None:
                return same, False
            if len(current) >= max_per_thread:
                raise TooManyUploads(upload.thread_id)
            stored = Upload(
                upload_id=uuid4(),
                thread_id=upload.thread_id,
                filename=upload.filename,
                kind=upload.kind,
                sha256=upload.sha256,
                size=len(upload.content),
                pages=upload.pages,
                sections=len(upload.sections),
                characters=upload.characters,
                warnings=upload.warnings,
                created_at=self._clock(),
            )
            self._uploads[stored.upload_id] = stored
            self._contents[stored.upload_id] = upload.content
            self._sections[stored.upload_id] = upload.sections
            return stored, True

    async def list_uploads(self, thread_id: str) -> list[Upload]:
        found = [u for u in self._uploads.values() if self._find(thread_id, u.upload_id)]
        return sorted(found, key=lambda upload: upload.created_at)

    async def get_upload(self, thread_id: str, upload_id: UUID) -> Upload | None:
        return self._find(thread_id, upload_id)

    async def read_sections(self, thread_id: str, upload_id: UUID) -> list[UploadSection]:
        if self._find(thread_id, upload_id) is None:
            return []
        return list(self._sections[upload_id])

    async def read_section(
        self, thread_id: str, upload_id: UUID, position: int
    ) -> UploadSection | None:
        sections = await self.read_sections(thread_id, upload_id)
        return sections[position] if 0 <= position < len(sections) else None

    async def read_content(self, thread_id: str, upload_id: UUID) -> bytes | None:
        if self._find(thread_id, upload_id) is None:
            return None
        return self._contents[upload_id]

    async def delete_upload(self, thread_id: str, upload_id: UUID) -> bool:
        if self._find(thread_id, upload_id) is None:
            return False
        self._forget(upload_id)
        return True

    async def delete_expired(self) -> int:
        expired = [u.upload_id for u in self._uploads.values() if self._expired(u)]
        for upload_id in expired:
            self._forget(upload_id)
        return len(expired)

    def _forget(self, upload_id: UUID) -> None:
        del self._uploads[upload_id], self._contents[upload_id], self._sections[upload_id]


def retention(settings: Settings) -> timedelta:
    """How long an upload is kept: UPLOAD_RETENTION_DAYS."""
    return timedelta(days=settings.upload_retention_days)
