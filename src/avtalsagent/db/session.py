"""Database connection.

What:
    Creates the SQLAlchemy engine and sessions from `DATABASE_URL`.

Why:
    One place decides how the code connects to Postgres, so tests can point
    it at their own database and no other module builds connections.

How:
    `create_db_engine()` uses the URL from settings (or one passed in by a
    test) with the psycopg 3 driver. `session_factory()` returns a factory
    whose sessions are used as `with factory.begin() as session: ...`. With
    `read_only=True` (avtal-mcp, M6) Postgres refuses every write on the
    engine's connections, and each transaction reads one snapshot
    (REPEATABLE READ), so a tool call never sees an index half rebuilt.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.config import get_settings


def create_db_engine(url: str | None = None, *, read_only: bool = False) -> Engine:
    database_url = url or str(get_settings().database_url)
    # "postgresql://" would make SQLAlchemy pick psycopg2; we use psycopg 3.
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if read_only:
        return create_engine(
            database_url,
            isolation_level="REPEATABLE READ",
            # Sent by libpq when the connection opens: an INSERT, UPDATE or DELETE fails.
            connect_args={"options": "-c default_transaction_read_only=on"},
        )
    return create_engine(database_url)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)
