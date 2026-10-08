"""Integration test: the user's files in Postgres (uploads/postgres_store.py, open_store.py).

What:
    Opens the store with UPLOAD_STORE=postgres twice (the second time finds
    its tables), adds an upload with its bytes and sections and reads them
    back; checks that another thread finds nothing, that the same file is
    one upload, that two uploads at once cannot pass the thread's limit,
    that all threads together cannot pass the store's limit in bytes, which
    does not count expired files, that deleting an upload removes its
    sections, and that an upload past
    the retention is not read and is deleted when the store opens.

Why:
    The unit tests run the memory store; only here do the SQL, the advisory
    locks, the cascade, `bytea` and `text[]` meet a real database, through
    psycopg's URL, which differs from SQLAlchemy's.

How:
    Uses the Postgres container of conftest.py. The store gets a database
    of its own on that server, dropped after each test: it creates its own
    tables, and `test_migrations.py` compares the main test database with
    the models.
"""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text

from avtalsagent.config import Settings
from avtalsagent.domain.uploads import NewUpload, UploadKind, UploadSection
from avtalsagent.uploads.open_store import open_upload_store
from avtalsagent.uploads.postgres_store import PostgresUploadStore
from avtalsagent.uploads.store import StoreFull, TooManyUploads

DATABASE = "upload_store"
THREAD = "tråd-1"
OTHER = "tråd-2"
CONTENT = b"%PDF-1.7\x00\xff binary bytes"
SECTIONS = (
    UploadSection(
        position=0, number=None, title="Avtal", level=0, page_start=1, text="Avtal om IT-drift"
    ),
    UploadSection(
        position=1,
        number="6.2",
        title="Ansvar",
        level=2,
        page_start=3,
        text="6.2 Ansvar\nLeverantören ansvarar för 'skador' och \"fel\".",
    ),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(engine: Engine) -> Iterator[Settings]:
    """UPLOAD_STORE=postgres on a database of its own, in SQLAlchemy's URL form."""
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT")  # no transaction around it
        connection.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        connection.execute(text(f"CREATE DATABASE {DATABASE}"))
    url = engine.url.set(database=DATABASE).render_as_string(hide_password=False)
    yield Settings(_env_file=None, upload_store="postgres", database_url=url)
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(f"DROP DATABASE {DATABASE} WITH (FORCE)"))


def new_upload(
    thread_id: str = THREAD,
    content: bytes = CONTENT,
    warnings: tuple[str, ...] = ("Sidan 2 har ingen text.",),
) -> NewUpload:
    return NewUpload(
        thread_id=thread_id,
        filename="Avtal å.pdf",
        kind=UploadKind.PDF,
        sha256=(content.hex() * 64)[:64],
        content=content,
        pages=3,
        characters=sum(len(section.text) for section in SECTIONS),
        warnings=warnings,
        sections=SECTIONS,
    )


def execute(settings: Settings, statement: str) -> None:
    engine = create_engine(str(settings.database_url))
    with engine.begin() as connection:
        connection.execute(text(statement))
    engine.dispose()


@pytest.mark.anyio
async def test_an_upload_is_stored_and_read_back_after_a_restart(settings: Settings) -> None:
    async with open_upload_store(settings) as store:
        assert isinstance(store, PostgresUploadStore)
        upload, created = await store.add_upload(new_upload(), max_per_thread=5)

    assert created
    async with open_upload_store(settings) as store:  # the tables are there already
        assert await store.list_uploads(THREAD) == [upload]
        assert await store.get_upload(THREAD, upload.upload_id) == upload
        assert await store.read_sections(THREAD, upload.upload_id) == list(SECTIONS)
        assert await store.read_section(THREAD, upload.upload_id, 1) == SECTIONS[1]
        assert await store.read_section(THREAD, upload.upload_id, 2) is None
        assert await store.read_content(THREAD, upload.upload_id) == CONTENT

    assert upload.filename == "Avtal å.pdf"
    assert (upload.size, upload.pages, upload.sections) == (len(CONTENT), 3, 2)
    assert upload.warnings == ("Sidan 2 har ingen text.",)
    assert upload.created_at.tzinfo is not None


@pytest.mark.anyio
async def test_another_thread_finds_nothing(settings: Settings) -> None:
    async with open_upload_store(settings) as store:
        upload, _ = await store.add_upload(new_upload(), max_per_thread=5)

        assert await store.list_uploads(OTHER) == []
        assert await store.get_upload(OTHER, upload.upload_id) is None
        assert await store.read_sections(OTHER, upload.upload_id) == []
        assert await store.read_section(OTHER, upload.upload_id, 0) is None
        assert await store.read_content(OTHER, upload.upload_id) is None
        assert not await store.delete_upload(OTHER, upload.upload_id)
        assert await store.get_upload(THREAD, upload.upload_id) == upload
        assert await store.get_upload(THREAD, uuid4()) is None


@pytest.mark.anyio
async def test_the_same_file_is_one_upload_per_thread(settings: Settings) -> None:
    async with open_upload_store(settings) as store:
        first, _ = await store.add_upload(new_upload(), max_per_thread=5)

        again, created = await store.add_upload(new_upload(), max_per_thread=5)
        elsewhere, created_elsewhere = await store.add_upload(
            new_upload(OTHER, warnings=()), max_per_thread=5
        )

    assert (again, created) == (first, False)
    assert created_elsewhere and elsewhere.upload_id != first.upload_id
    assert elsewhere.warnings == ()


@pytest.mark.anyio
async def test_uploads_at_the_same_time_cannot_pass_the_limit(settings: Settings) -> None:
    async with open_upload_store(settings) as store:
        results = await asyncio.gather(
            *(
                store.add_upload(new_upload(content=f"fil {n}".encode()), max_per_thread=2)
                for n in range(4)
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(result, TooManyUploads) for result in results) == 2
        assert len(await store.list_uploads(THREAD)) == 2


@pytest.mark.anyio
async def test_all_threads_together_have_at_most_max_total_bytes(settings: Settings) -> None:
    async with open_upload_store(settings) as store:
        first, _ = await store.add_upload(
            new_upload(content=b"a" * 60), max_per_thread=5, max_total_bytes=100
        )
        with pytest.raises(StoreFull):
            await store.add_upload(
                new_upload(OTHER, content=b"b" * 41), max_per_thread=5, max_total_bytes=100
            )
        _, created = await store.add_upload(
            new_upload(OTHER, content=b"b" * 40), max_per_thread=5, max_total_bytes=100
        )
        assert created
        execute(
            settings,
            "UPDATE upload SET created_at = now() - interval '8 days' "
            f"WHERE id = '{first.upload_id}'",
        )
        # The expired file is not counted, before or after a sweep deletes it.
        _, created = await store.add_upload(
            new_upload("tråd-3", content=b"c" * 60), max_per_thread=5, max_total_bytes=100
        )
        assert created


@pytest.mark.anyio
async def test_deleting_an_upload_removes_its_sections(settings: Settings) -> None:
    async with open_upload_store(settings) as store:
        upload, _ = await store.add_upload(new_upload(), max_per_thread=5)

        assert await store.delete_upload(THREAD, upload.upload_id)
        assert not await store.delete_upload(THREAD, upload.upload_id)
        assert await store.list_uploads(THREAD) == []

    engine = create_engine(str(settings.database_url))
    with engine.connect() as connection:
        left = connection.execute(text("SELECT count(*) FROM upload_section")).scalar_one()
    engine.dispose()
    assert left == 0


@pytest.mark.anyio
async def test_an_expired_upload_is_not_read_and_is_deleted_when_the_store_opens(
    settings: Settings,
) -> None:
    async with open_upload_store(settings) as store:
        old, _ = await store.add_upload(new_upload(content=b"gammal"), max_per_thread=5)
        young, _ = await store.add_upload(new_upload(content=b"ny"), max_per_thread=5)
        execute(
            settings,
            "UPDATE upload SET created_at = now() - interval '8 days' "
            f"WHERE id = '{old.upload_id}'",
        )

        assert await store.list_uploads(THREAD) == [young]
        assert await store.get_upload(THREAD, old.upload_id) is None
        assert await store.read_sections(THREAD, old.upload_id) == []
        assert await store.read_content(THREAD, old.upload_id) is None
        # Uploaded again, the expired file is a new upload, not blocked by the old row.
        again, created = await store.add_upload(new_upload(content=b"gammal"), max_per_thread=5)
        assert created and again.upload_id != old.upload_id

    execute(settings, "UPDATE upload SET created_at = now() - interval '8 days'")
    async with open_upload_store(settings) as store:
        assert await store.delete_expired() == 0  # opening deleted them already
        assert await store.list_uploads(THREAD) == []


@pytest.mark.anyio
async def test_retention_follows_the_setting(settings: Settings) -> None:
    short = settings.model_copy(update={"upload_retention_days": 1})
    async with open_upload_store(short) as store:
        assert isinstance(store, PostgresUploadStore)
        assert store._retention == timedelta(days=1)
        upload, _ = await store.add_upload(new_upload(), max_per_thread=5)
        execute(settings, "UPDATE upload SET created_at = now() - interval '2 days'")

        assert await store.get_upload(THREAD, upload.upload_id) is None
        assert await store.delete_expired() == 1
