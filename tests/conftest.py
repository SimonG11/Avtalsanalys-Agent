"""Shared test fixtures.

What:
    `sample_register_xlsx` builds an xlsx file from tests/fixtures/register_sample.tsv.

Why:
    The sample rows are real rows from "Alla giltiga ramavtal" (2026-10-05).
    Keeping them as TSV makes them readable in review; the fixture turns them
    into the same xlsx layout as the real file.

How:
    Row 1 is the title row, row 2 the headers, then the data rows. Date columns
    are written as Excel dates, the other cells as text, as in the real file.
"""

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

FIXTURES = Path(__file__).parent / "fixtures"
DATE_COLUMNS = {5, 6, 7}  # Giltig från, Giltig till, Max förl. till (0-based)


def write_register_xlsx(rows: list[list[str]], target: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for number, row in enumerate(rows, start=1):
        if number > 2:  # data rows: real dates in date columns, empty cells as None
            sheet.append(
                [
                    (date.fromisoformat(cell) if i in DATE_COLUMNS else cell) if cell else None
                    for i, cell in enumerate(row)
                ]
            )
        else:
            sheet.append(row)
    workbook.save(target)
    return target


def read_sample_tsv() -> list[list[str]]:
    lines = (FIXTURES / "register_sample.tsv").read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines]


@pytest.fixture
def sample_register_xlsx(tmp_path: Path) -> Path:
    return write_register_xlsx(read_sample_tsv(), tmp_path / "giltiga-ramavtal.xlsx")
