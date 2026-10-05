"""Database tables, defined with SQLAlchemy 2.

What:
    The register tables built from the Excel master list: list version,
    procurement, supplier, supplier name, agreement, sub-area and the link
    between agreements and sub-areas. Later milestones add document tables.

Why:
    The Excel list has one row per supplier and sub-area. Splitting it into
    these tables gives each fact one place: the supplier lives on the
    agreement, dates on the agreement's sub-area (they differ per sub-area in
    the real list), the sub-area hierarchy in `sub_area`, and a supplier is
    identified by its organisation number, never by its name (one org number
    can have several names, e.g. hotels in the same company).

How:
    Each class is one table. Alembic migrations in `db/migrations/` create
    the tables; `register/load.py` fills them. The tables mirror the most
    recently loaded list; `register_version` records every load.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RegisterVersion(Base):
    """One load of the master list, identified by the date in its title row."""

    __tablename__ = "register_version"

    list_date: Mapped[date] = mapped_column(Date, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    row_count: Mapped[int] = mapped_column(Integer)
    rejected_count: Mapped[int] = mapped_column(Integer)


class Procurement(Base):
    """A procurement (upphandling), identified by its case number (diarienummer)."""

    __tablename__ = "procurement"

    # One procurement can span several framework areas (23.3-2965-20 does), so
    # the framework area is not stored here; it is reached through sub_area.
    procurement_number: Mapped[str] = mapped_column(String(32), primary_key=True)


class Supplier(Base):
    """A supplier, identified by its normalised organisation number.

    Swedish numbers are NNNNNN-NNNN; foreign ones are kept as written
    (see `domain.identifiers.normalize_org_number`).
    """

    __tablename__ = "supplier"

    org_number: Mapped[str] = mapped_column(String(20), primary_key=True)


class SupplierName(Base):
    """A name a supplier appears under; one org number can have several."""

    __tablename__ = "supplier_name"

    org_number: Mapped[str] = mapped_column(ForeignKey("supplier.org_number"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    former_name: Mapped[str | None] = mapped_column(Text)


class Agreement(Base):
    """A framework agreement between one supplier and Statens inköpscentral."""

    __tablename__ = "agreement"

    agreement_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    procurement_number: Mapped[str] = mapped_column(
        ForeignKey("procurement.procurement_number"), index=True
    )
    sequence: Mapped[str | None] = mapped_column(String(8))
    org_number: Mapped[str] = mapped_column(ForeignKey("supplier.org_number"), index=True)
    supplier_name: Mapped[str] = mapped_column(Text)  # the name on this agreement's rows


class SubArea(Base):
    """One level of a sub-area path, e.g. "Gävle" under "Gävleborgs län"."""

    __tablename__ = "sub_area"
    __table_args__ = (UniqueConstraint("framework_area", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    framework_area: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)  # full path, levels joined with " / "
    name: Mapped[str] = mapped_column(Text)  # the last level of the path
    level: Mapped[int] = mapped_column(Integer)  # 1 = top level
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sub_area.id"))


class AgreementSubArea(Base):
    """Which sub-areas an agreement covers, and when (one Excel row = one link)."""

    __tablename__ = "agreement_sub_area"

    agreement_number: Mapped[str] = mapped_column(
        ForeignKey("agreement.agreement_number"), primary_key=True
    )
    sub_area_id: Mapped[int] = mapped_column(ForeignKey("sub_area.id"), primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    max_extension_to: Mapped[date | None] = mapped_column(Date)
