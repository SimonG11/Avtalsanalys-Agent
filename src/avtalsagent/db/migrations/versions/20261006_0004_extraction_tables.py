"""Create the extraction tables (M4) and agreement_page.missing_since.

What:
    Creates document_metadata, document_fact, document_reference,
    reference_target and validation_finding, as defined in `db/models.py`, and
    adds the column missing_since to agreement_page.

Why:
    Step 4 of the ingestion stores what it found in each file (metadata, facts,
    references and their targets) and step 5 the findings that keep a file or
    a section out of the index. A page that avropa.se no longer lists is
    marked rather than deleted, so its files can be held back with a reason.

How:
    Written to match `alembic revision --autogenerate` and reviewed by hand.
    The four extraction tables are deleted with their file or section (ON
    DELETE CASCADE), like the section tables. validation_finding has no
    foreign key, so a finding can name a file without a parsed_file row.
    `downgrade` drops the tables in reverse order and the column.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agreement_page", sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "document_metadata",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("type_rule", sa.String(length=32), nullable=False),
        sa.Column("document_group", sa.String(length=16), nullable=False),
        sa.Column("binding", sa.Boolean(), nullable=False),
        sa.Column("agreement_number", sa.String(length=40), nullable=True),
        sa.Column("annex_number", sa.String(length=32), nullable=True),
        sa.Column("first_chapter", sa.Integer(), nullable=True),
        sa.Column("tendsign_cover", sa.String(length=32), nullable=True),
        sa.Column("is_template", sa.Boolean(), nullable=False),
        sa.Column("version_date", sa.Date(), nullable=True),
        sa.Column("version_rule", sa.String(length=32), nullable=True),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("site_updated", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["sha256"], ["parsed_file.sha256"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.create_index(
        op.f("ix_document_metadata_agreement_number"),
        "document_metadata",
        ["agreement_number"],
        unique=False,
    )
    op.create_table(
        "document_fact",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("rule", sa.String(length=32), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("statement", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["sha256"], ["parsed_file.sha256"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_fact_sha256"), "document_fact", ["sha256"], unique=False)
    op.create_table(
        "document_reference",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("section_position", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("document_name", sa.Text(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("replaces", sa.Boolean(), nullable=False),
        sa.Column("pattern_rule", sa.String(length=32), nullable=False),
        sa.Column("mention_status", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolve_rule", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["sha256", "section_position"],
            ["document_section.sha256", "document_section.position"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_reference_section",
        "document_reference",
        ["sha256", "section_position"],
        unique=False,
    )
    op.create_table(
        "reference_target",
        sa.Column("reference_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("target_sha256", sa.String(length=64), nullable=False),
        sa.Column("target_section_position", sa.Integer(), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["target_sha256", "target_section_position"],
            ["document_section.sha256", "document_section.position"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["reference_id"], ["document_reference.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_sha256"], ["parsed_file.sha256"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("reference_id", "position"),
    )
    op.create_index(
        "ix_reference_target_section",
        "reference_target",
        ["target_sha256", "target_section_position"],
        unique=False,
    )
    op.create_table(
        "validation_finding",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("check_name", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("section_position", sa.Integer(), nullable=True),
        sa.Column("agreement_number", sa.String(length=40), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("accepted_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "severity <> 'quarantine' OR sha256 IS NOT NULL", name="quarantine_names_a_file"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_validation_finding_sha256"), "validation_finding", ["sha256"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_validation_finding_sha256"), table_name="validation_finding")
    op.drop_table("validation_finding")
    op.drop_index("ix_reference_target_section", table_name="reference_target")
    op.drop_table("reference_target")
    op.drop_index("ix_document_reference_section", table_name="document_reference")
    op.drop_table("document_reference")
    op.drop_index(op.f("ix_document_fact_sha256"), table_name="document_fact")
    op.drop_table("document_fact")
    op.drop_index(op.f("ix_document_metadata_agreement_number"), table_name="document_metadata")
    op.drop_table("document_metadata")
    op.drop_column("agreement_page", "missing_since")
