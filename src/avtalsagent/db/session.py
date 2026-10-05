"""Database connection.

What:
    Creates the SQLAlchemy engine and sessions from `DATABASE_URL`.

Why:
    One place decides how the code connects to Postgres, so tests can point
    it at their own database and no other module builds connections.

How:
    `create_db_engine()` uses the URL from settings (or one passed in by a
    test) with the psycopg 3 driver. `session_factory()` returns a factory
    whose sessions are used as `with factory.begin() as session: ...`.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.config import get_settings


def create_db_engine(url: str | None = None) -> Engine:
    database_url = url or str(get_settings().database_url)
    # "postgresql://" would make SQLAlchemy pick psycopg2; we use psycopg 3.
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(database_url)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)
