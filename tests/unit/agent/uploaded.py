"""The user's files for the agent's tests: a short contract, read and stored as the API would.

What:
    `CONTRACT`, a contract in Markdown with five numbered clauses;
    `new_upload` reads bytes into a `NewUpload` with the API's parser, and
    `add` stores one in a store; `DownStore` is a store whose database does
    not answer.

Why:
    The tools, the prompt and the check read real sections, cut by the same
    parser as an upload through the API, so a test shows what the agent
    would see of a real file.
"""

import hashlib
from datetime import timedelta
from uuid import UUID

from avtalsagent.domain.uploads import NewUpload, Upload, UploadSection
from avtalsagent.uploads.parse import UploadLimits, parse_upload
from avtalsagent.uploads.store import MemoryUploadStore, UploadStoreUnavailable

LIMITS = UploadLimits(max_bytes=10 * 1024 * 1024, max_pages=300, max_characters=1_500_000)
CONTRACT = """\
# Avropsavtal (exempel)

## 1. Parter
Exempelkommunen och Konsultbolaget Exempel AB.

## 2. Kontraktstid
Kontraktet gäller från och med 2026-10-01 till och med 2027-09-30.

## 3. Fakturering
Leverantören har rätt att ta ut en faktureringsavgift om 45 kronor per faktura.

## 4. Skadestånd och ansvarsbegränsning
Leverantörens totala skadeståndsansvar är begränsat till 10 procent av Kontraktets värde.

## 5. Uppsägning
Kunden får säga upp Kontraktet med 14 kalenderdagars uppsägningstid.
""".encode()
# The positions of the clauses: the text before the first heading is 0.
LIABILITY = 4
LIABILITY_QUOTE = "skadeståndsansvar är begränsat till 10 procent av Kontraktets värde"


def new_upload(thread_id: str, filename: str, data: bytes) -> NewUpload:
    parsed = parse_upload(data, filename, LIMITS)
    return NewUpload(
        thread_id=thread_id,
        filename=filename,
        kind=parsed.kind,
        sha256=hashlib.sha256(data).hexdigest(),
        content=data,
        pages=parsed.pages,
        characters=parsed.characters,
        warnings=parsed.warnings,
        sections=parsed.sections,
    )


def memory_store() -> MemoryUploadStore:
    return MemoryUploadStore(timedelta(days=7))


async def add(
    store: MemoryUploadStore, thread_id: str, filename: str = "avtal.md", data: bytes = CONTRACT
) -> Upload:
    upload, _ = await store.add_upload(new_upload(thread_id, filename, data), 5)
    return upload


class DownStore:
    """An `UploadStore` whose database does not answer."""

    async def add_upload(
        self, upload: NewUpload, max_per_thread: int, *, max_total_bytes: int | None = None
    ) -> tuple[Upload, bool]:
        raise UploadStoreUnavailable("connection refused")

    async def list_uploads(self, thread_id: str) -> list[Upload]:
        raise UploadStoreUnavailable("connection refused")

    async def get_upload(self, thread_id: str, upload_id: UUID) -> Upload | None:
        raise UploadStoreUnavailable("connection refused")

    async def read_sections(self, thread_id: str, upload_id: UUID) -> list[UploadSection]:
        raise UploadStoreUnavailable("connection refused")

    async def read_section(
        self, thread_id: str, upload_id: UUID, position: int
    ) -> UploadSection | None:
        raise UploadStoreUnavailable("connection refused")

    async def read_content(self, thread_id: str, upload_id: UUID) -> bytes | None:
        raise UploadStoreUnavailable("connection refused")

    async def delete_upload(self, thread_id: str, upload_id: UUID) -> bool:
        raise UploadStoreUnavailable("connection refused")

    async def delete_expired(self) -> int:
        raise UploadStoreUnavailable("connection refused")
