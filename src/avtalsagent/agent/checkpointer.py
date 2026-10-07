"""Where the agent keeps a conversation's checkpoints: in memory or in Postgres.

What:
    `open_checkpointer(settings)` yields the checkpointer CHECKPOINTER names:
    `InMemorySaver` ("memory", the command line) or `AsyncPostgresSaver`
    on a connection pool ("postgres", the API; ADR 0004), both with
    `serializer()`.
    `checkpoint_dsn` is DATABASE_URL as psycopg takes it.

Why:
    A checkpoint lets a run stop at `ask_user` and go on when the user
    answers, and keeps a conversation's earlier questions. The state holds
    the agent's own Pydantic objects (the draft `FinalAnswer` and the checked
    `Answer`), and LangGraph's serializer gives back an object's class only
    when the class is allowed: an unknown one comes back as a dict, at once
    with LANGGRAPH_STRICT_MSGPACK=true and in every case in a later version,
    and `draft.answered` would fail after a restart. The allowlist names the
    agent's classes; every other class outside LangGraph's own safe types
    stays a dict, so a checkpoint cannot make the process import and run
    code.

How:
    `JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)`, the
    classes of `schemas.py` that the state holds; with an explicit list the
    serializer is strict whether LANGGRAPH_STRICT_MSGPACK is set or not
    (LangGraph reads it once, at import). Postgres: psycopg takes a libpq URL,
    so DATABASE_URL is read as SQLAlchemy reads it (`make_url`, as the rest
    of the system connects) and written out again without the driver
    ("+psycopg"), its password percent-encoded the way libpq decodes it: a
    password libpq would read differently, such as one with a bare "%", then
    works here too. The saver takes its connection from a pool of one
    (`POOL_SIZE`), which checks the connection before lending it: a
    connection that Postgres has closed (a restart) is replaced instead of
    failing every later run. The saver runs one checkpoint operation at a
    time (its own lock), so the API's runs take turns on that connection
    for each read and write, and a second connection would stand unused.
    `setup()` creates or migrates the checkpoint tables each time
    the checkpointer opens, once per process. LangGraph's tables live in the
    same database as the models but are not theirs: the migrations leave
    them out (`db/migrations/env.py`).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from avtalsagent.agent.schemas import Answer, Citation, DraftCitation, FinalAnswer
from avtalsagent.config import Settings

# The agent's classes in a checkpoint. The nested ones (the citations) are stored inside
# their parent and rebuilt by it; they are listed so that they come back as classes alone too.
CHECKPOINT_TYPES: tuple[type, ...] = (FinalAnswer, DraftCitation, Answer, Citation)

# The connections the saver holds. AsyncPostgresSaver runs one operation at a time behind its
# own lock, whether it has a connection or a pool, so one is all it uses.
POOL_SIZE = 1


def serializer() -> JsonPlusSerializer:
    """LangGraph's serializer, giving back the agent's classes and only LangGraph's others."""
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


def checkpoint_dsn(database_url: str) -> str:
    """The database URL without SQLAlchemy's driver: "postgresql+psycopg://…" → "postgresql://…".

    The parts are SQLAlchemy's reading of the URL, written out with the
    password encoded for libpq, so both read the same password.
    """
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[str]]:
    """The checkpointer CHECKPOINTER names, open until the block ends."""
    if settings.checkpointer == "memory":
        yield InMemorySaver(serde=serializer())
        return
    pool = AsyncConnectionPool(
        checkpoint_dsn(str(settings.database_url)),
        min_size=POOL_SIZE,
        max_size=POOL_SIZE,
        # What the saver needs of a connection, as its own from_conn_string sets it; the
        # class says the rows are dicts, for the type checker.
        connection_class=AsyncConnection[DictRow],
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
        open=False,  # opened by the block below, and closed when it ends
    )
    async with pool:
        saver = AsyncPostgresSaver(pool, serde=serializer())
        await saver.setup()
        yield saver
