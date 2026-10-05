"""One normalised row of the framework-agreement master list.

What:
    `RegisterRow`: a row of "Alla giltiga ramavtal" after normalisation, and
    `RegisterVersion`: which edition of the list the rows come from.

Why:
    The Excel list has one row per supplier and sub-area, not one per
    agreement. `RegisterRow` keeps exactly that shape, with every identifier
    already normalised, so the database layer only has to group rows.

How:
    `register/normalize.py` builds `RegisterRow` objects from raw Excel rows.
    `register/load.py` groups them into procurements, suppliers, agreements
    and sub-areas. The row key is agreement number + org number + sub-area.
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

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        """The row key: agreement number + org number + sub-area."""
        return (self.agreement_number, self.org_number, self.sub_area_path)
