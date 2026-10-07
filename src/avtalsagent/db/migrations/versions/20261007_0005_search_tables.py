"""Create the search tables (M5) and the pgvector extension.

What:
    Creates the extension `vector` if it is missing, and the tables
    document_scope, search_chunk, search_term, index_build and
    embedding_cache, as defined in `db/models.py`.

Why:
    Step 6 builds the search index: each chunk the quarantine lets through,
    with its embedding and its BM25 weights, the words of the BM25 index, the
    scope each file can be filtered on, and how the index was built. The
    embedding cache outlives the index, so a rebuild embeds only new texts.
    The extension is created here because the migrations must work on any
    database with pgvector installed: docker/postgres/init.sql creates it only
    when the compose volume is new, and the integration tests never run it.

How:
    Written to match `alembic revision --autogenerate` and reviewed by hand.
    search_chunk and document_scope are deleted with their chunk or file (ON
    DELETE CASCADE). The vector columns have no dimension, so the embedding
    model can change without a migration; the search is exact (no vector
    index), which needs none (ADR 0011). `downgrade` drops the tables but
    keeps the extension, which other databases on the server may use.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import SPARSEVEC, Vector

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_scope",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("framework_areas", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("procurement_numbers", sa.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("agreement_numbers", sa.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("page_titles", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["sha256"], ["parsed_file.sha256"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.create_index(
        "ix_document_scope_framework_areas",
        "document_scope",
        ["framework_areas"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_document_scope_procurement_numbers",
        "document_scope",
        ["procurement_numbers"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_document_scope_agreement_numbers",
        "document_scope",
        ["agreement_numbers"],
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_document_scope_document_type"), "document_scope", ["document_type"], unique=False
    )
    op.create_table(
        "search_chunk",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("section_position", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("section_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("term_weights", SPARSEVEC(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sha256", "section_position", "position"],
            ["section_chunk.sha256", "section_chunk.section_position", "section_chunk.position"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sha256", "section_position", "position"),
    )
    op.create_index(
        op.f("ix_search_chunk_section_hash"), "search_chunk", ["section_hash"], unique=False
    )
    op.create_table(
        "search_term",
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("term"),
        sa.UniqueConstraint("id"),
    )
    op.create_table(
        "index_build",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("analyser", sa.Text(), nullable=False),
        sa.Column("term_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("held_back_count", sa.Integer(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="one_index_build"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "embedding_cache",
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("model", "text_hash"),
    )


def downgrade() -> None:
    op.drop_table("embedding_cache")
    op.drop_table("index_build")
    op.drop_table("search_term")
    op.drop_index(op.f("ix_search_chunk_section_hash"), table_name="search_chunk")
    op.drop_table("search_chunk")
    op.drop_index(op.f("ix_document_scope_document_type"), table_name="document_scope")
    op.drop_index("ix_document_scope_agreement_numbers", table_name="document_scope")
    op.drop_index("ix_document_scope_procurement_numbers", table_name="document_scope")
    op.drop_index("ix_document_scope_framework_areas", table_name="document_scope")
    op.drop_table("document_scope")
