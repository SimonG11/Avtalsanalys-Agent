"""Create the section tables (M3).

What:
    Creates parsed_file, document_section and section_chunk, as defined in
    `db/models.py`.

Why:
    Step 3 of the ingestion splits each parsed file into numbered sections
    and cuts long sections into chunks for search (see docs/steg/03-tolkning.md).

How:
    Generated with `alembic revision --autogenerate` and reviewed by hand.
    Sections and chunks are deleted with their file (ON DELETE CASCADE), so a
    new run can replace everything with one DELETE. `downgrade` drops the
    tables in reverse order.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parsed_file",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("parser", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("pages_needing_ocr", sa.ARRAY(sa.Integer()), nullable=False),
        sa.Column("outline", sa.String(length=16), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "chunked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.create_table(
        "document_section",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent_position", sa.Integer(), nullable=True),
        sa.Column("path", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["sha256"], ["parsed_file.sha256"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sha256", "position"),
    )
    op.create_table(
        "section_chunk",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("section_position", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("context_header", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sha256", "section_position"],
            ["document_section.sha256", "document_section.position"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sha256", "section_position", "position"),
    )


def downgrade() -> None:
    op.drop_table("section_chunk")
    op.drop_table("document_section")
    op.drop_table("parsed_file")
