"""Create the register tables (M1).

What:
    Creates register_version, procurement, supplier, supplier_name, agreement,
    sub_area and agreement_sub_area, as defined in `db/models.py`.

Why:
    The Excel master list is split into these tables so each fact has one
    place (see docs/steg/01-register.md).

How:
    Generated with `alembic revision --autogenerate` and reviewed by hand.
    `downgrade` drops the tables in reverse order.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procurement",
        sa.Column("procurement_number", sa.String(length=32), nullable=False),
        sa.Column("framework_area", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("procurement_number"),
    )
    op.create_table(
        "register_version",
        sa.Column("list_date", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("list_date"),
    )
    op.create_table(
        "sub_area",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("framework_area", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["sub_area.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("framework_area", "path"),
    )
    op.create_table(
        "supplier",
        sa.Column("org_number", sa.String(length=11), nullable=False),
        sa.PrimaryKeyConstraint("org_number"),
    )
    op.create_table(
        "agreement",
        sa.Column("agreement_number", sa.String(length=40), nullable=False),
        sa.Column("procurement_number", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.String(length=8), nullable=False),
        sa.Column("org_number", sa.String(length=11), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("max_extension_to", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_number"],
            ["supplier.org_number"],
        ),
        sa.ForeignKeyConstraint(
            ["procurement_number"],
            ["procurement.procurement_number"],
        ),
        sa.PrimaryKeyConstraint("agreement_number"),
    )
    op.create_index(op.f("ix_agreement_org_number"), "agreement", ["org_number"], unique=False)
    op.create_index(
        op.f("ix_agreement_procurement_number"), "agreement", ["procurement_number"], unique=False
    )
    op.create_table(
        "supplier_name",
        sa.Column("org_number", sa.String(length=11), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("former_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_number"],
            ["supplier.org_number"],
        ),
        sa.PrimaryKeyConstraint("org_number", "name"),
    )
    op.create_table(
        "agreement_sub_area",
        sa.Column("agreement_number", sa.String(length=40), nullable=False),
        sa.Column("sub_area_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agreement_number"],
            ["agreement.agreement_number"],
        ),
        sa.ForeignKeyConstraint(
            ["sub_area_id"],
            ["sub_area.id"],
        ),
        sa.PrimaryKeyConstraint("agreement_number", "sub_area_id"),
    )


def downgrade() -> None:
    op.drop_table("agreement_sub_area")
    op.drop_table("supplier_name")
    op.drop_index(op.f("ix_agreement_procurement_number"), table_name="agreement")
    op.drop_index(op.f("ix_agreement_org_number"), table_name="agreement")
    op.drop_table("agreement")
    op.drop_table("supplier")
    op.drop_table("sub_area")
    op.drop_table("register_version")
    op.drop_table("procurement")
