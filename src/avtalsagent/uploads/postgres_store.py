"""The user's files in Postgres, in two tables the API creates itself.

What:
    `PostgresUploadStore`, the `UploadStore` (`store.py`) on a psycopg
    connection pool, and `open_postgres_upload_store(settings)`, which
    opens the pool on DATABASE_URL, creates the tables when they are
    missing and closes the pool when the block ends. `UPLOAD_TABLES` names
    the tables, which the migrations leave out (`db/migrations/env.py`).

Why:
    One database (ADR 0004), and the API owns these tables as it owns
    LangGraph's checkpoint tables: it creates them when it starts, so the
    demo needs `docker compose up` and no new run of the ingestion, whose
    migrations would take ten minutes to reach. They hold the conversation's
    files, not the corpus: avtal-mcp, read-only over the corpus, never reads
    them. A file's bytes are kept with it (at most UPLOAD_MAX_BYTES, a few
    per conversation, for a week), so the API needs no writable volume.

How:
    `upload` has one row per file with its bytes (`content`) and is unique
    on (thread_id, sha256); `upload_section` has its sections and goes with
    it (ON DELETE CASCADE). The tables are created with CREATE TABLE IF NOT
    EXISTS under an advisory lock, so two processes starting together do not
    collide; a later change to them needs a step of its own here, as
    LangGraph migrates its tables. Every read filters on the thread and on
    `created_at` within the retention, so an expired file is never read
    before `delete_expired` removes it. `add_upload` takes an advisory lock
    on the thread for its transaction, so two uploads to one thread at the
    same time cannot pass the limit or store the same file twice, and with
    `max_total_bytes` one more lock, the same for all threads, so that two
    threads cannot together pass the store's limit. Each
    operation is one transaction on a connection from the pool (at most
    `POOL_SIZE`), which checks a connection before lending it, so one that
    Postgres closed (a restart) is replaced. A psycopg error, a pool
    timeout included, becomes `UploadStoreUnavailable`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from avtalsagent.agent.checkpointer import checkpoint_dsn
from avtalsagent.config import Settings
from avtalsagent.domain.uploads import NewUpload, Upload, UploadKind, UploadSection
from avtalsagent.uploads.store import (
    StoreFull,
    TooManyUploads,
    UploadStoreUnavailable,
    retention,
)

UPLOAD_TABLES = frozenset({"upload", "upload_section"})

# Connections the store may hold: an upload, the agent's reads and the file route at once.
POOL_SIZE = 4

SCHEMA = (
    # The key of the lock that two starting processes take before creating the tables.
    "SELECT pg_advisory_xact_lock(hashtext('avtalsagent upload tables'))",
    """
    CREATE TABLE IF NOT EXISTS upload (
        id uuid PRIMARY KEY,
        thread_id text NOT NULL,
        filename text NOT NULL,
        kind text NOT NULL,
        sha256 text NOT NULL,
        size integer NOT NULL,
        pages integer,
        sections integer NOT NULL,
        characters integer NOT NULL,
        warnings text[] NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        content bytea NOT NULL,
        UNIQUE (thread_id, sha256)
    )
    """,
    "CREATE INDEX IF NOT EXISTS upload_created_at ON upload (created_at)",
    """
    CREATE TABLE IF NOT EXISTS upload_section (
        upload_id uuid NOT NULL REFERENCES upload (id) ON DELETE CASCADE,
        position integer NOT NULL,
        number text,
        title text NOT NULL,
        level integer NOT NULL,
        page_start integer,
        text text NOT NULL,
        PRIMARY KEY (upload_id, position)
    )
    """,
)

_COLUMNS = (
    "id, thread_id, filename, kind, sha256, size, pages, sections, characters, warnings, created_at"
)
# An upload the thread may read: its own, and not older than the retention.
_LIVE = "thread_id = %(thread_id)s AND created_at >= now() - %(retention)s"
_SECTION_COLUMNS = "position, number, title, level, page_start, text"


class PostgresUploadStore:
    """`UploadStore` in the `upload` and `upload_section` tables (see the module)."""

    def __init__(
        self, pool: AsyncConnectionPool[AsyncConnection[DictRow]], retention: timedelta
    ) -> None:
        self._pool = pool
        self._retention = retention

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        """A connection in a transaction; a database error is `UploadStoreUnavailable`."""
        try:
            async with self._pool.connection() as connection, connection.transaction():
                yield connection
        except psycopg.Error as error:
            raise UploadStoreUnavailable(str(error)) from error

    async def create_tables(self) -> None:
        """Create the tables and their index when they are missing."""
        async with self._transaction() as connection:
            for statement in SCHEMA:
                await connection.execute(statement)

    def _live(self, thread_id: str, **more: Any) -> dict[str, Any]:
        return {"thread_id": thread_id, "retention": self._retention, **more}

    async def add_upload(
        self, upload: NewUpload, max_per_thread: int, *, max_total_bytes: int | None = None
    ) -> tuple[Upload, bool]:
        async with self._transaction() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"upload {upload.thread_id}",)
            )
            # An expired copy of the same file would otherwise block the new one.
            await connection.execute(
                "DELETE FROM upload WHERE thread_id = %(thread_id)s "
                "AND created_at < now() - %(retention)s",
                self._live(upload.thread_id),
            )
            cursor = await connection.execute(
                f"SELECT {_COLUMNS} FROM upload WHERE {_LIVE} ORDER BY created_at, id",
                self._live(upload.thread_id),
            )
            current = [_upload(row) for row in await cursor.fetchall()]
            same = next((stored for stored in current if stored.sha256 == upload.sha256), None)
            if same is not None:
                return same, False
            if len(current) >= max_per_thread:
                raise TooManyUploads(upload.thread_id)
            if max_total_bytes is not None:
                # Taken after the thread's lock, always in that order, so two uploads never wait
                # for each other's lock.
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('avtalsagent upload total'))"
                )
                # Expired files are not counted (they are deleted within the hour), as the memory
                # store deletes them first.
                cursor = await connection.execute(
                    "SELECT coalesce(sum(size), 0) AS total FROM upload "
                    "WHERE created_at >= now() - %s",
                    (self._retention,),
                )
                total = await cursor.fetchone()
                assert total is not None  # an aggregate gives one row
                if total["total"] + len(upload.content) > max_total_bytes:
                    raise StoreFull(upload.thread_id)
            cursor = await connection.execute(
                "INSERT INTO upload (id, thread_id, filename, kind, sha256, size, pages, "
                "sections, characters, warnings, content) VALUES (gen_random_uuid(), "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {_COLUMNS}",
                (
                    upload.thread_id,
                    upload.filename,
                    upload.kind.value,
                    upload.sha256,
                    len(upload.content),
                    upload.pages,
                    len(upload.sections),
                    upload.characters,
                    list(upload.warnings),
                    upload.content,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None  # INSERT ... RETURNING gives the row
            stored = _upload(row)
            async with connection.cursor() as sections:
                await sections.executemany(
                    f"INSERT INTO upload_section (upload_id, {_SECTION_COLUMNS}) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            stored.upload_id,
                            section.position,
                            section.number,
                            section.title,
                            section.level,
                            section.page_start,
                            section.text,
                        )
                        for section in upload.sections
                    ],
                )
            return stored, True

    async def list_uploads(self, thread_id: str) -> list[Upload]:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                f"SELECT {_COLUMNS} FROM upload WHERE {_LIVE} ORDER BY created_at, id",
                self._live(thread_id),
            )
            return [_upload(row) for row in await cursor.fetchall()]

    async def get_upload(self, thread_id: str, upload_id: UUID) -> Upload | None:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                f"SELECT {_COLUMNS} FROM upload WHERE id = %(id)s AND {_LIVE}",
                self._live(thread_id, id=upload_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _upload(row)

    async def read_sections(self, thread_id: str, upload_id: UUID) -> list[UploadSection]:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                f"SELECT {_SECTION_COLUMNS} FROM upload_section WHERE upload_id = %(id)s "
                f"AND EXISTS (SELECT 1 FROM upload WHERE id = %(id)s AND {_LIVE}) "
                "ORDER BY position",
                self._live(thread_id, id=upload_id),
            )
            return [UploadSection(**row) for row in await cursor.fetchall()]

    async def read_section(
        self, thread_id: str, upload_id: UUID, position: int
    ) -> UploadSection | None:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                f"SELECT {_SECTION_COLUMNS} FROM upload_section WHERE upload_id = %(id)s "
                "AND position = %(position)s "
                f"AND EXISTS (SELECT 1 FROM upload WHERE id = %(id)s AND {_LIVE})",
                self._live(thread_id, id=upload_id, position=position),
            )
            row = await cursor.fetchone()
        return None if row is None else UploadSection(**row)

    async def read_content(self, thread_id: str, upload_id: UUID) -> bytes | None:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                f"SELECT content FROM upload WHERE id = %(id)s AND {_LIVE}",
                self._live(thread_id, id=upload_id),
            )
            row = await cursor.fetchone()
        return None if row is None else bytes(row["content"])

    async def delete_upload(self, thread_id: str, upload_id: UUID) -> bool:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                f"DELETE FROM upload WHERE id = %(id)s AND {_LIVE}",
                self._live(thread_id, id=upload_id),
            )
            return cursor.rowcount > 0

    async def delete_expired(self) -> int:
        async with self._transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM upload WHERE created_at < now() - %s", (self._retention,)
            )
            return cursor.rowcount


def _upload(row: DictRow) -> Upload:
    return Upload(
        upload_id=row["id"],
        thread_id=row["thread_id"],
        filename=row["filename"],
        kind=UploadKind(row["kind"]),
        sha256=row["sha256"],
        size=row["size"],
        pages=row["pages"],
        sections=row["sections"],
        characters=row["characters"],
        warnings=tuple(row["warnings"]),
        created_at=row["created_at"],
    )


@asynccontextmanager
async def open_postgres_upload_store(settings: Settings) -> AsyncIterator[PostgresUploadStore]:
    """The store on a pool to DATABASE_URL, its tables created, open until the block ends."""
    pool = AsyncConnectionPool(
        checkpoint_dsn(str(settings.database_url)),
        min_size=1,
        max_size=POOL_SIZE,
        connection_class=AsyncConnection[DictRow],
        kwargs={"autocommit": True, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
        open=False,  # opened by the block below, and closed when it ends
    )
    async with pool:
        store = PostgresUploadStore(pool, retention(settings))
        await store.create_tables()
        yield store
