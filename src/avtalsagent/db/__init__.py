"""Database layer: tables, connection and migrations.

What:
    SQLAlchemy table definitions (`models.py`), engine and sessions
    (`session.py`) and Alembic migrations (`migrations/`).

Why:
    All database details live here, so the rest of the code works with
    typed objects and never builds SQL strings by hand.

How:
    Run `uv run alembic upgrade head` to create or update the tables.
"""
