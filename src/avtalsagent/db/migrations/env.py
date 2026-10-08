"""Alembic environment: how migrations connect to the database.

What:
    Runs the migration scripts in `versions/` against the database.

Why:
    Migrations make every schema change a reviewed, versioned file, so any
    database can be brought to the same state with one command. LangGraph's
    checkpoint tables (`CHECKPOINT_TABLES`, ADR 0013) share the database but
    are not the models': LangGraph creates and migrates them itself, the
    first time the API opens its checkpointer. The tables of the user's
    uploaded files (`UPLOAD_TABLES`) are the API's in the same way: it
    creates them when it opens its upload store. Without a filter,
    `alembic revision --autogenerate` would see them as tables the models
    lack and write a migration that drops them.

How:
    The URL comes from an `sqlalchemy.url` set by the caller (the tests do
    this) or else from the settings via `create_db_engine()`. `Base.metadata`
    lets `alembic revision --autogenerate` compare the models with the database;
    `include_object` leaves LangGraph's and the uploads' tables out of the
    comparison.
    `alembic upgrade head --sql` (offline mode) prints the SQL instead of
    running it, so a migration can be read and checked without a database.
"""

from alembic import context
from sqlalchemy.schema import SchemaItem

from avtalsagent.db.models import Base
from avtalsagent.db.session import create_db_engine
from avtalsagent.uploads.postgres_store import UPLOAD_TABLES

# The tables LangGraph's Postgres checkpointer creates (langgraph-checkpoint-postgres).
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_object(
    item: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Whether autogenerate compares the object: everything but the API's own tables."""
    return not (type_ == "table" and name in CHECKPOINT_TABLES | UPLOAD_TABLES)


url = context.config.get_main_option("sqlalchemy.url")
engine = create_db_engine(url)

if context.is_offline_mode():
    context.configure(
        url=engine.url,
        target_metadata=Base.metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()
