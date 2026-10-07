"""Tests for avtalsagent.uploads.open_store and the Postgres store's errors, without a database.

UPLOAD_STORE=memory gives the memory store with the settings' retention;
postgres opens the Postgres store and deletes the expired uploads before the
API uses it. A database error in the Postgres store, a pool timeout
included, is `UploadStoreUnavailable`, which the routes answer with 503. The
store's SQL runs against Postgres in tests/integration.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from avtalsagent.config import Settings
from avtalsagent.uploads import open_store
from avtalsagent.uploads.postgres_store import PostgresUploadStore
from avtalsagent.uploads.store import MemoryUploadStore, UploadStoreUnavailable


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_memory_is_the_default_with_the_settings_retention() -> None:
    settings = Settings(_env_file=None, upload_retention_days=3)

    async with open_store.open_upload_store(settings) as store:
        assert isinstance(store, MemoryUploadStore)
        assert store._retention == timedelta(days=3)


class FakePostgresStore:
    def __init__(self) -> None:
        self.log: list[str] = []

    async def delete_expired(self) -> int:
        self.log.append("delete expired")
        return 0


@pytest.mark.anyio
async def test_postgres_opens_its_store_and_deletes_the_expired_uploads_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePostgresStore()
    opened: list[Settings] = []

    @asynccontextmanager
    async def open_postgres(settings: Settings) -> AsyncIterator[FakePostgresStore]:
        opened.append(settings)
        fake.log.append("open")
        yield fake
        fake.log.append("close")

    monkeypatch.setattr(open_store, "open_postgres_upload_store", open_postgres)
    settings = Settings(_env_file=None, upload_store="postgres")

    async with open_store.open_upload_store(settings) as store:
        assert store is fake  # type: ignore[comparison-overlap]
        assert fake.log == ["open", "delete expired"]

    assert fake.log[-1] == "close"
    assert opened == [settings]


class FailingPool:
    """A pool whose connections fail as `error` says."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        raise self.error
        yield  # pragma: no cover


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError("server closed the connection unexpectedly"),
        PoolTimeout("couldn't get a connection after 30.00 sec"),
    ],
)
async def test_a_database_error_is_the_store_being_unavailable(error: Exception) -> None:
    store = PostgresUploadStore(FailingPool(error), timedelta(days=7))  # type: ignore[arg-type]

    with pytest.raises(UploadStoreUnavailable):
        await store.list_uploads("tråd-1")
    with pytest.raises(UploadStoreUnavailable):
        await store.read_section("tråd-1", uuid4(), 0)
