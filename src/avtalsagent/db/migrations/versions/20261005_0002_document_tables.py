"""Create the document tables (M2).

What:
    Creates agreement_page, source_document and agreement_page_document, as
    defined in `db/models.py`.

Why:
    Step 1 of the ingestion records which pages on avropa.se were read, which
    files were downloaded (with hash and version) and which page links to which
    file (see docs/steg/02-hamtning.md).

How:
    Generated with `alembic revision --autogenerate` and reviewed by hand.
    `downgrade` drops the tables in reverse order.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agreement_page",
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("procurement_numbers", sa.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("agreement_period", sa.Text(), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("url"),
    )
    op.create_table(
        "source_document",
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("url"),
    )
    op.create_index(op.f("ix_source_document_sha256"), "source_document", ["sha256"], unique=False)
    op.create_table(
        "agreement_page_document",
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("document_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("agreement_number", sa.String(length=40), nullable=True),
        sa.Column("site_updated", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_url"],
            ["source_document.url"],
        ),
        sa.ForeignKeyConstraint(
            ["page_url"],
            ["agreement_page.url"],
        ),
        sa.PrimaryKeyConstraint("page_url", "document_url"),
    )
    op.create_index(
        op.f("ix_agreement_page_document_agreement_number"),
        "agreement_page_document",
        ["agreement_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agreement_page_document_agreement_number"), table_name="agreement_page_document"
    )
    op.drop_table("agreement_page_document")
    op.drop_index(op.f("ix_source_document_sha256"), table_name="source_document")
    op.drop_table("source_document")
    op.drop_table("agreement_page")
