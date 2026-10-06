"""Shared test fixtures.

What:
    `sample_register_xlsx` builds an xlsx file from tests/fixtures/register_sample.tsv.

Why:
    The sample rows are the first rows of "Alla giltiga ramavtal" (2026-10-05),
    so each Excel row number is the same as in the real file. Keeping them as
    TSV makes them readable in review; the fixture turns them into the same
    xlsx layout as the real file.

How:
    Row 1 is the title row, row 2 the headers, then the data rows. Every cell is
    a text cell, as in the real file: dates are ISO text ("2025-04-03") and an
    empty cell holds the empty string. openpyxl saves "" as a cell without text,
    which reads back as None, so an empty cell is written as empty rich text,
    which reads back as "".
"""

from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText

FIXTURES = Path(__file__).parent / "fixtures"


def write_register_xlsx(rows: list[list[str]], target: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append([cell or CellRichText("") for cell in row])
    workbook.save(target)
    return target


def read_sample_tsv() -> list[list[str]]:
    lines = (FIXTURES / "register_sample.tsv").read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines]


@pytest.fixture
def sample_register_xlsx(tmp_path: Path) -> Path:
    return write_register_xlsx(read_sample_tsv(), tmp_path / "giltiga-ramavtal.xlsx")
