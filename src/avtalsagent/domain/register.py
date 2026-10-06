"""One normalised row of the framework-agreement master list.

What:
    `RegisterRow`: a row of "Alla giltiga ramavtal" after normalisation, and
    `RegisterVersion`: which edition of the list the rows come from.
    `RegisterEntry`: one agreement in one sub-area as read back from the
    register tables, the form step 5 of the ingestion compares documents with.

Why:
    The Excel list has one row per supplier and sub-area, not one per
    agreement. `RegisterRow` keeps exactly that shape, with every identifier
    already normalised, so the database layer only has to group rows.

How:
    `register/normalize.py` builds `RegisterRow` objects from raw Excel rows.
    `register/load.py` groups them into procurements, suppliers, agreements
    and sub-areas. A row is identified by agreement number + org number +
    framework area + sub-area, because the sub-area hierarchy is built per
    framework area ("Gävleborgs län" is in three of them).
"""

from datetime import date

from pydantic import BaseModel, ConfigDict


class RegisterVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    # The date in the title row, e.g. "Giltiga ramavtal 2026-10-05".
    list_date: date
    title: str


class RegisterRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Excel row number (1-based), so every finding can point back to the file.
    source_row: int

    agreement_number: str  # 23.3-14537-2023-001
    procurement_number: str  # 23.3-14537-2023 (diarienummer)
    sequence: str | None  # 001; None when the number has no sequence (23.5-3718-2024)

    supplier_name: str
    former_supplier_name: str | None
    org_number: str  # NNNNNN-NNNN, or a foreign number as written (FI01148912)

    framework_area: str  # Ramavtalsområde, e.g. "Bemanningstjänster"
    sub_area_path: tuple[str, ...]  # Delområde split on " / "

    # The dates belong to this row (agreement + sub-area), not to the whole
    # agreement: one agreement can start at different dates in different sub-areas.
    valid_from: date
    valid_to: date
    max_extension_to: date | None  # "Max förl. till", often empty


class RegisterEntry(BaseModel):
    """One agreement in one sub-area, read back from the register tables."""

    model_config = ConfigDict(frozen=True)

    agreement_number: str
    procurement_number: str
    org_number: str
    supplier_name: str
    former_supplier_name: str | None
    framework_area: str
    sub_area_path: tuple[str, ...]
    valid_from: date
    valid_to: date
    max_extension_to: date | None

    @classmethod
    def from_row(cls, row: RegisterRow) -> "RegisterEntry":
        return cls.model_validate(row.model_dump(exclude={"source_row", "sequence"}))
