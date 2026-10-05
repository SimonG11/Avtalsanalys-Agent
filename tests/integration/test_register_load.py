"""Integration test: migrations and register load against a real Postgres.

What:
    Starts Postgres 17 + pgvector in Docker (testcontainers), runs the Alembic
    migrations, loads the sample register and checks the tables.

Why:
    Unit tests cover the logic; this test proves the SQL works: foreign keys,
    the delete-and-insert order and that loading twice gives the same result.

How:
    The `database_url` fixture starts one container for the whole module, with
    the same image as docker-compose.yml. Requires Docker.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select, text
from testcontainers.community.postgres import PostgresContainer

from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.register.load import LoadReport, load_register
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register

IMAGE = "pgvector/pgvector:0.8.7-pg17-bookworm"  # same as docker-compose.yml
ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
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


def load_sample(engine: Engine, xlsx: Path) -> LoadReport:
    raw = read_register(xlsx)
    normalized = normalize_rows(raw.rows)
    with session_factory(engine).begin() as session:
        return load_register(session, raw.version, normalized.rows, normalized.rejected)


def count(engine: Engine, model: type[models.Base]) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(model)).scalar_one()


def test_sample_register_is_loaded(engine: Engine, sample_register_xlsx: Path) -> None:
    report = load_sample(engine, sample_register_xlsx)

    assert report.rows_loaded == 18
    assert count(engine, models.Agreement) == 9
    assert count(engine, models.AgreementSubArea) == 18
    assert count(engine, models.Supplier) == 4
    assert count(engine, models.SupplierName) == 5


def test_loading_twice_gives_the_same_tables(engine: Engine, sample_register_xlsx: Path) -> None:
    load_sample(engine, sample_register_xlsx)
    first = {m.__tablename__: count(engine, m) for m in models.Base.__subclasses__()}

    load_sample(engine, sample_register_xlsx)
    second = {m.__tablename__: count(engine, m) for m in models.Base.__subclasses__()}

    assert first == second
    assert second["register_version"] == 1


def test_agreement_can_be_found_with_its_sub_areas(
    engine: Engine, sample_register_xlsx: Path
) -> None:
    load_sample(engine, sample_register_xlsx)

    with engine.connect() as connection:
        regions = connection.execute(
            text(
                """
                SELECT s.name FROM agreement_sub_area l
                JOIN sub_area s ON s.id = l.sub_area_id
                WHERE l.agreement_number = '23.3-14537-2023-001'
                  AND s.path LIKE '%upp till 1000 timmar%'
                ORDER BY s.name
                """
            )
        ).scalars()

        assert list(regions) == [
            "Mellersta Norrland",
            "Småland med öarna",
            "Stockholm",
            "Sydsverige",
        ]
