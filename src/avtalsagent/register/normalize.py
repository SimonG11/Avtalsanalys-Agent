"""Turn raw Excel rows into normalised `RegisterRow` objects.

What:
    `normalize_rows` converts every `RawRow` into a `RegisterRow` and collects
    the rows it cannot convert, with the reason. `find_conflicts` checks that
    rows sharing an agreement number agree on the supplier, and that no
    agreement + sub-area appears twice. `find_foreign_org_numbers` lists the
    suppliers with a non-Swedish registration number, so they can be checked
    by hand.

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
    is_swedish_org_number,
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
    """Report rows that contradict an earlier row.

    The supplier belongs to the agreement, so every row with the same
    agreement number must carry the same org number. Dates do not: in the
    real list one agreement often has different dates per sub-area. A row
    key (agreement + org number + framework area + sub-area) must appear only once.

    A procurement may span several framework areas (23.3-2965-20 does), so
    that is not a conflict.
    """
    first_agreement: dict[str, RegisterRow] = {}
    first_key: dict[tuple[str, str, str, tuple[str, ...]], RegisterRow] = {}
    findings: list[Finding] = []
    for row in rows:
        first = first_agreement.setdefault(row.agreement_number, row)
        if row.org_number != first.org_number:
            findings.append(
                Finding(
                    row.source_row,
                    f"agreement {row.agreement_number} has org number {row.org_number}, "
                    f"row {first.source_row} has {first.org_number}",
                )
            )
        key = (row.agreement_number, row.org_number, row.framework_area, row.sub_area_path)
        first = first_key.setdefault(key, row)
        if first is not row:
            findings.append(
                Finding(
                    row.source_row,
                    f"agreement {row.agreement_number} and sub-area "
                    f"{' / '.join(row.sub_area_path)!r} already on row {first.source_row}",
                )
            )
    return findings


def find_foreign_org_numbers(rows: Iterable[RegisterRow]) -> list[Finding]:
    """List each supplier whose org number is not Swedish, once, at its first row.

    Foreign numbers are accepted as written. Listing them makes a mistyped
    Swedish number (e.g. 9 digits) visible instead of silently accepted.
    """
    seen: set[str] = set()
    findings: list[Finding] = []
    for row in rows:
        if is_swedish_org_number(row.org_number) or row.org_number in seen:
            continue
        seen.add(row.org_number)
        findings.append(
            Finding(row.source_row, f"foreign org number {row.org_number} ({row.supplier_name})")
        )
    return findings


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
