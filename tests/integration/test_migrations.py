"""Integration test: the migrations build exactly the schema of the models.

What:
    Compares the database that `alembic upgrade head` built (the `engine`
    fixture) with `models.Base.metadata`, as `alembic revision --autogenerate`
    would, and expects no difference: no table, column, type, index, unique
    constraint or foreign key that one has and the other lacks.

Why:
    The code reads and writes through the models, but the database is built
    by the migrations. A difference fails only at run time, or never: a
    missing index or cascade goes unnoticed. Migration 0005 adds pgvector's
    `vector` and `sparsevec` columns and GIN indexes on arrays, which only a
    real database can reflect.

How:
    `alembic.autogenerate.compare_metadata` on a connection of the `engine`
    fixture (Postgres 17 + pgvector, all migrations applied), with column
    types compared. Server defaults are not compared, as in autogenerate.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from avtalsagent.db.models import Base


def test_the_migrations_build_the_schema_of_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == []
