"""Open the upload store that UPLOAD_STORE names, as the API starts, and keep it swept.

What:
    `open_upload_store(settings)` yields `MemoryUploadStore` ("memory") or
    `PostgresUploadStore` ("postgres", its tables created), with the
    uploads older than UPLOAD_RETENTION_DAYS deleted, deletes them again
    every `SWEEP_SECONDS` while it is open, and closes it when the block
    ends.

Why:
    The setting chooses the store as CHECKPOINTER chooses the checkpointer
    (`agent/checkpointer.py`): memory by default, for the tests, the command
    line and a run without a database, and Postgres in the API's container.
    Expired uploads are deleted when the API starts, once an hour and on
    each upload (`api/uploads.py`), so a file is gone within an hour of its
    seventh day even when the API runs for weeks and nobody uploads
    anything. They are never read after the seventh day.

How:
    An async context manager, which the API's lifespan enters with its
    other connections. The sweep is a task that sleeps and deletes; a
    store that does not answer is logged and tried again at the next sweep,
    and the task is cancelled when the block ends.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from avtalsagent.config import Settings
from avtalsagent.uploads.postgres_store import open_postgres_upload_store
from avtalsagent.uploads.store import (
    MemoryUploadStore,
    UploadStore,
    UploadStoreUnavailable,
    retention,
)

_log = logging.getLogger(__name__)

SWEEP_SECONDS = 3600.0


@asynccontextmanager
async def open_upload_store(
    settings: Settings, *, sweep_seconds: float = SWEEP_SECONDS
) -> AsyncIterator[UploadStore]:
    """The store UPLOAD_STORE names, its expired uploads deleted, open until the block ends."""
    async with _opened(settings) as store:
        sweeper = asyncio.create_task(_sweep(store, sweep_seconds))
        try:
            yield store
        finally:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)


@asynccontextmanager
async def _opened(settings: Settings) -> AsyncIterator[UploadStore]:
    if settings.upload_store == "memory":
        yield MemoryUploadStore(retention(settings))
        return
    async with open_postgres_upload_store(settings) as store:
        await store.delete_expired()
        yield store


async def _sweep(store: UploadStore, seconds: float) -> None:
    """Delete the expired uploads every `seconds`, until cancelled."""
    while True:
        await asyncio.sleep(seconds)
        try:
            deleted = await store.delete_expired()
        except UploadStoreUnavailable as error:
            _log.warning("the upload store did not answer; the sweep is tried again: %s", error)
            continue
        if deleted:
            _log.info("deleted %d expired uploads", deleted)
