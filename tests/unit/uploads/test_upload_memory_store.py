"""Tests for avtalsagent.uploads.store: the uploads in memory, per conversation.

An upload is read back with its sections and bytes by its own thread only;
the same file in a thread is the same upload; a thread has at most
`max_per_thread`, and all threads together at most `max_total_bytes`;
deleting removes it; an upload past the retention is not
read and is deleted by `delete_expired`. The clock is a variable the test
moves. The same behaviour in Postgres is in
tests/integration/test_upload_store_postgres.py.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from avtalsagent.domain.uploads import NewUpload, UploadKind, UploadSection
from avtalsagent.uploads.store import MemoryUploadStore, StoreFull, TooManyUploads

THREAD = "tråd-1"
OTHER = "tråd-2"
SECTIONS = (
    UploadSection(
        position=0, number="1", title="Parter", level=1, page_start=1, text="1 Parter\nA och B."
    ),
    UploadSection(
        position=1, number="2", title="Pris", level=1, page_start=2, text="2 Pris\nFem kronor."
    ),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def new_upload(thread_id: str = THREAD, content: bytes = b"%PDF-1.7 avtal") -> NewUpload:
    return NewUpload(
        thread_id=thread_id,
        filename="avtal.pdf",
        kind=UploadKind.PDF,
        sha256=content.hex()[:64].ljust(64, "0"),
        content=content,
        pages=2,
        characters=sum(len(section.text) for section in SECTIONS),
        warnings=("Sidan 3 har ingen text.",),
        sections=SECTIONS,
    )


@pytest.mark.anyio
async def test_an_upload_is_read_back_by_its_thread() -> None:
    store = MemoryUploadStore(timedelta(days=7), Clock())

    upload, created = await store.add_upload(new_upload(), max_per_thread=5)

    assert created
    assert upload.thread_id == THREAD
    assert (upload.size, upload.pages, upload.sections) == (len(b"%PDF-1.7 avtal"), 2, 2)
    assert upload.warnings == ("Sidan 3 har ingen text.",)
    assert await store.list_uploads(THREAD) == [upload]
    assert await store.get_upload(THREAD, upload.upload_id) == upload
    assert await store.read_sections(THREAD, upload.upload_id) == list(SECTIONS)
    assert await store.read_section(THREAD, upload.upload_id, 1) == SECTIONS[1]
    assert await store.read_section(THREAD, upload.upload_id, 2) is None
    assert await store.read_section(THREAD, upload.upload_id, -1) is None
    assert await store.read_content(THREAD, upload.upload_id) == b"%PDF-1.7 avtal"


@pytest.mark.anyio
async def test_another_thread_does_not_find_the_upload() -> None:
    store = MemoryUploadStore(timedelta(days=7), Clock())
    upload, _ = await store.add_upload(new_upload(), max_per_thread=5)

    assert await store.list_uploads(OTHER) == []
    assert await store.get_upload(OTHER, upload.upload_id) is None
    assert await store.read_sections(OTHER, upload.upload_id) == []
    assert await store.read_section(OTHER, upload.upload_id, 0) is None
    assert await store.read_content(OTHER, upload.upload_id) is None
    assert not await store.delete_upload(OTHER, upload.upload_id)
    assert await store.get_upload(THREAD, upload.upload_id) == upload  # still there
    assert await store.get_upload(THREAD, uuid4()) is None


@pytest.mark.anyio
async def test_the_same_file_again_in_a_thread_is_the_same_upload() -> None:
    store = MemoryUploadStore(timedelta(days=7), Clock())
    first, _ = await store.add_upload(new_upload(), max_per_thread=5)

    again, created = await store.add_upload(new_upload(), max_per_thread=5)
    elsewhere, created_elsewhere = await store.add_upload(new_upload(OTHER), max_per_thread=5)

    assert (again, created) == (first, False)
    assert created_elsewhere and elsewhere.upload_id != first.upload_id
    assert len(await store.list_uploads(THREAD)) == 1


@pytest.mark.anyio
async def test_a_thread_has_at_most_max_per_thread_uploads() -> None:
    clock = Clock()
    store = MemoryUploadStore(timedelta(days=7), clock)
    for n in range(2):
        clock.now += timedelta(minutes=1)
        await store.add_upload(new_upload(content=f"fil {n}".encode()), max_per_thread=2)

    with pytest.raises(TooManyUploads):
        await store.add_upload(new_upload(content=b"fil 3"), max_per_thread=2)
    # The same file as one already stored is not a new one, and other threads have their own.
    _, created = await store.add_upload(new_upload(content=b"fil 0"), max_per_thread=2)
    assert not created
    await store.add_upload(new_upload(OTHER, content=b"fil 3"), max_per_thread=2)
    assert [u.filename for u in await store.list_uploads(THREAD)] == ["avtal.pdf"] * 2


@pytest.mark.anyio
async def test_all_threads_together_have_at_most_max_total_bytes() -> None:
    clock = Clock()
    store = MemoryUploadStore(timedelta(days=7), clock)
    await store.add_upload(new_upload(content=b"a" * 60), max_per_thread=5, max_total_bytes=100)

    # Thread ids are the client's to choose: a new one does not get more space.
    with pytest.raises(StoreFull):
        await store.add_upload(
            new_upload(OTHER, content=b"b" * 41), max_per_thread=5, max_total_bytes=100
        )
    fits, created = await store.add_upload(
        new_upload(OTHER, content=b"b" * 40), max_per_thread=5, max_total_bytes=100
    )
    assert created and fits.size == 40
    # The same file again takes no more space, and an expired file none at all.
    _, created = await store.add_upload(
        new_upload(content=b"a" * 60), max_per_thread=5, max_total_bytes=100
    )
    assert not created
    clock.now += timedelta(days=8)
    _, created = await store.add_upload(
        new_upload("tråd-3", content=b"c" * 100), max_per_thread=5, max_total_bytes=100
    )
    assert created


@pytest.mark.anyio
async def test_uploads_are_listed_oldest_first_and_deleted_one_by_one() -> None:
    clock = Clock()
    store = MemoryUploadStore(timedelta(days=7), clock)
    first, _ = await store.add_upload(new_upload(content=b"en"), max_per_thread=5)
    clock.now += timedelta(seconds=1)
    second, _ = await store.add_upload(new_upload(content=b"tva"), max_per_thread=5)

    assert await store.list_uploads(THREAD) == [first, second]
    assert await store.delete_upload(THREAD, first.upload_id)
    assert not await store.delete_upload(THREAD, first.upload_id)
    assert await store.list_uploads(THREAD) == [second]
    assert await store.read_sections(THREAD, first.upload_id) == []


@pytest.mark.anyio
async def test_an_upload_past_the_retention_is_not_read_and_is_deleted() -> None:
    clock = Clock()
    store = MemoryUploadStore(timedelta(days=7), clock)
    old, _ = await store.add_upload(new_upload(content=b"gammal"), max_per_thread=5)
    clock.now += timedelta(days=6)
    young, _ = await store.add_upload(new_upload(content=b"ny"), max_per_thread=5)
    clock.now += timedelta(days=1, seconds=1)

    assert await store.list_uploads(THREAD) == [young]
    assert await store.get_upload(THREAD, old.upload_id) is None
    assert await store.read_content(THREAD, old.upload_id) is None
    assert await store.delete_expired() == 1
    assert await store.delete_expired() == 0
    # The expired file can be uploaded again, as a new upload.
    again, created = await store.add_upload(new_upload(content=b"gammal"), max_per_thread=5)
    assert created and again.upload_id != old.upload_id
