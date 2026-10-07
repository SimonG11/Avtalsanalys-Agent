"""Open the upload store that UPLOAD_STORE names, as the API starts.

What:
    `open_upload_store(settings)` yields `MemoryUploadStore` ("memory") or
    `PostgresUploadStore` ("postgres", its tables created), with the
    uploads older than UPLOAD_RETENTION_DAYS deleted, and closes it when
    the block ends.

Why:
    The setting chooses the store as CHECKPOINTER chooses the checkpointer
    (`agent/checkpointer.py`): memory by default, for the tests, the command
    line and a run without a database, and Postgres in the API's container.
    Expired uploads are deleted when the API starts, and on each upload
    (`api/uploads.py`), so a file is gone a week after it came even when
    nobody uploads anything for a while.

How:
    An async context manager, which the API's lifespan enters with its
    other connections.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from avtalsagent.config import Settings
from avtalsagent.uploads.postgres_store import open_postgres_upload_store
from avtalsagent.uploads.store import MemoryUploadStore, UploadStore, retention


@asynccontextmanager
async def open_upload_store(settings: Settings) -> AsyncIterator[UploadStore]:
    """The store UPLOAD_STORE names, its expired uploads deleted, open until the block ends."""
    if settings.upload_store == "memory":
        yield MemoryUploadStore(retention(settings))
        return
    async with open_postgres_upload_store(settings) as store:
        await store.delete_expired()
        yield store
