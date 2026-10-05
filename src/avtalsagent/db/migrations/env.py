"""Alembic environment: how migrations connect to the database.

What:
    Runs the migration scripts in `versions/` against the database.

Why:
    Migrations make every schema change a reviewed, versioned file, so any
    database can be brought to the same state with one command.

How:
    The URL comes from an `sqlalchemy.url` set by the caller (the tests do
    this) or else from the settings via `create_db_engine()`. `Base.metadata`
    lets `alembic revision --autogenerate` compare the models with the database.
"""

from alembic import context

from avtalsagent.db.models import Base
from avtalsagent.db.session import create_db_engine

url = context.config.get_main_option("sqlalchemy.url")
engine = create_db_engine(url)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
