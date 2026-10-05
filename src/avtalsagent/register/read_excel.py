"""Read the Excel master list "Alla giltiga ramavtal" into raw rows.

What:
    Opens the xlsx file, reads the list date from the title row, checks the
    header row and returns every data row as a `RawRow`, without interpreting
    the values.

Why:
    Reading and interpreting are kept apart. This module only knows the file
    layout (title row, header row, data rows), so a layout change on avropa.se
    fails here with a clear message instead of producing wrong data later.

How:
    Row 1 is a title such as "Giltiga ramavtal 2026-10-05"; its date becomes
    the list version. Row 2 must contain exactly `EXPECTED_HEADERS`. Every
    following non-empty row becomes a `RawRow` keyed by header name.
    `register/normalize.py` turns the raw rows into `RegisterRow` objects.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from avtalsagent.domain.register import RegisterVersion

EXPECTED_HEADERS = (
    "Avtalsnummer",
    "Leverantör",
    "Organisationsnummer",
    "Ramavtalsområde",
    "Delområde",
    "Giltig från",
    "Giltig till",
    "Max förl. till",
)

_TITLE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# What a cell can contain after openpyxl has read it.
CellValue = str | int | float | date | datetime | None


class RegisterFormatError(ValueError):
    """Raised when the file does not have the expected layout."""


@dataclass(frozen=True)
class RawRow:
    source_row: int  # 1-based row number in the sheet
    values: dict[str, CellValue]  # header -> cell value


@dataclass(frozen=True)
class RawRegister:
    version: RegisterVersion
    rows: list[RawRow]


def read_register(path: Path) -> RawRegister:
    """Read the first sheet of the master list at `path`."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)

        title_row = _cells(next(rows, ()))
        header_row = _cells(next(rows, ()))
        if not title_row or not header_row:
            raise RegisterFormatError("the sheet has fewer than two rows")

        version = _read_version(title_row)
        _check_headers(header_row)

        raw_rows = [
            RawRow(source_row=number, values=dict(zip(EXPECTED_HEADERS, cells, strict=False)))
            for number, cells in enumerate(map(_cells, rows), start=3)
            if any(cell not in (None, "") for cell in cells)
        ]
    finally:
        workbook.close()
    return RawRegister(version=version, rows=raw_rows)


def _cells(row: tuple[object, ...]) -> tuple[CellValue, ...]:
    """Keep plain values as they are and turn anything else (e.g. rich text) into text."""
    return tuple(
        cell if cell is None or isinstance(cell, str | int | float | date) else str(cell)
        for cell in row
    )


def _read_version(title_row: tuple[CellValue, ...]) -> RegisterVersion:
    title = str(title_row[0] or "").strip()
    match = _TITLE_DATE.search(title)
    if match is None:
        raise RegisterFormatError(f"no date in the title row: {title!r}")
    return RegisterVersion(list_date=date.fromisoformat(match.group()), title=title)


def _check_headers(header_row: tuple[CellValue, ...]) -> None:
    headers = tuple(str(cell or "").strip() for cell in header_row[: len(EXPECTED_HEADERS)])
    if headers != EXPECTED_HEADERS:
        raise RegisterFormatError(f"unexpected headers: {headers}, expected {EXPECTED_HEADERS}")
