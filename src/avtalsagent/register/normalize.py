"""Turn raw Excel rows into normalised `RegisterRow` objects.

What:
    `normalize_rows` converts every `RawRow` into a `RegisterRow` and collects
    the rows it cannot convert, with the reason. `find_conflicts` checks that
    rows sharing an agreement number agree on supplier and dates, and that
    rows sharing a procurement agree on framework area.

Why:
    A bad row must never disappear silently or stop the whole import. Each
    problem becomes a `Finding` that points to the Excel row, so it can be
    reported and checked by hand.

How:
    Identifiers go through `domain/identifiers.py`. Dates may arrive as Excel
    dates or as ISO text, and are converted to `date`. All functions are pure.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from avtalsagent.domain.identifiers import (
    IdentifierError,
    normalize_org_number,
    parse_agreement_number,
    parse_supplier_name,
    split_sub_area,
)
from avtalsagent.domain.register import RegisterRow
from avtalsagent.register.read_excel import CellValue, RawRow


@dataclass(frozen=True)
class Finding:
    """A problem with one Excel row, kept for the import report."""

    source_row: int
    message: str


@dataclass(frozen=True)
class NormalizeResult:
    rows: list[RegisterRow]
    rejected: list[Finding]


def normalize_rows(raw_rows: Iterable[RawRow]) -> NormalizeResult:
    rows: list[RegisterRow] = []
    rejected: list[Finding] = []
    for raw in raw_rows:
        try:
            rows.append(normalize_row(raw))
        except ValueError as error:  # IdentifierError is a ValueError
            rejected.append(Finding(raw.source_row, str(error)))
    return NormalizeResult(rows=rows, rejected=rejected)


def normalize_row(raw: RawRow) -> RegisterRow:
    values = raw.values
    agreement = parse_agreement_number(_text(values.get("Avtalsnummer"), "Avtalsnummer"))
    supplier = parse_supplier_name(_text(values.get("Leverantör"), "Leverantör"))
    return RegisterRow(
        source_row=raw.source_row,
        agreement_number=agreement.full,
        procurement_number=agreement.procurement_number,
        sequence=agreement.sequence,
        supplier_name=supplier.name,
        former_supplier_name=supplier.former_name,
        org_number=normalize_org_number(
            _text(values.get("Organisationsnummer"), "Organisationsnummer")
        ),
        framework_area=_text(values.get("Ramavtalsområde"), "Ramavtalsområde"),
        sub_area_path=split_sub_area(_text(values.get("Delområde"), "Delområde")),
        valid_from=_required_date(values.get("Giltig från"), "Giltig från"),
        valid_to=_required_date(values.get("Giltig till"), "Giltig till"),
        max_extension_to=_optional_date(values.get("Max förl. till"), "Max förl. till"),
    )


def find_conflicts(rows: Iterable[RegisterRow]) -> list[Finding]:
    """Report rows that contradict an earlier row for the same agreement or procurement.

    Supplier org number and the three dates belong to the agreement, so every
    row with the same agreement number must carry the same values. Likewise,
    one procurement belongs to one framework area.
    """
    first_agreement: dict[str, RegisterRow] = {}
    first_procurement: dict[str, RegisterRow] = {}
    findings: list[Finding] = []
    for row in rows:
        first = first_agreement.setdefault(row.agreement_number, row)
        if _agreement_fields(row) != _agreement_fields(first):
            findings.append(
                Finding(
                    row.source_row,
                    f"agreement {row.agreement_number} differs from row {first.source_row} "
                    "(org number or dates)",
                )
            )
        first = first_procurement.setdefault(row.procurement_number, row)
        if row.framework_area != first.framework_area:
            findings.append(
                Finding(
                    row.source_row,
                    f"procurement {row.procurement_number} has framework area "
                    f"{row.framework_area!r}, row {first.source_row} has {first.framework_area!r}",
                )
            )
    return findings


def _agreement_fields(row: RegisterRow) -> tuple[object, ...]:
    return (row.org_number, row.valid_from, row.valid_to, row.max_extension_to)


def _text(value: CellValue, column: str) -> str:
    if value is None:
        raise IdentifierError(f"{column} is empty")
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a number stored as a number, e.g. an org number
    text = str(value).strip()
    if not text:
        raise IdentifierError(f"{column} is empty")
    return text


def _required_date(value: CellValue, column: str) -> date:
    parsed = _optional_date(value, column)
    if parsed is None:
        raise IdentifierError(f"{column} is empty")
    return parsed


def _optional_date(value: CellValue, column: str) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):  # check before date: datetime is a subclass of date
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise IdentifierError(f"{column} is not a date: {value!r}") from None
