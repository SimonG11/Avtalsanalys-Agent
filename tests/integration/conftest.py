"""Shared fixtures for the integration tests.

What:
    `engine`: a SQLAlchemy engine on a fresh Postgres 17 + pgvector container
    with all Alembic migrations applied.

Why:
    The integration tests prove the SQL works against the real database. One
    container per test session keeps them fast; each test module cleans or
    replaces the rows it uses.

How:
    testcontainers starts the same image as docker-compose.yml. Requires Docker.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from testcontainers.community.postgres import PostgresContainer

from avtalsagent.db.session import create_db_engine

IMAGE = "pgvector/pgvector:0.8.7-pg17-bookworm"  # same as docker-compose.yml
ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    with PostgresContainer(IMAGE, driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        config = Config(ROOT / "alembic.ini")
        config.set_main_option("script_location", str(ROOT / "src/avtalsagent/db/migrations"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        engine = create_db_engine(url)
        yield engine
        engine.dispose()
