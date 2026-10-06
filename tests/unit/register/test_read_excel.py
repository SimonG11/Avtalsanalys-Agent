"""Tests for avtalsagent.register.read_excel: file layout only, no interpretation."""

from datetime import date
from pathlib import Path

import pytest

from avtalsagent.register.read_excel import RegisterFormatError, read_register
from tests.conftest import read_sample_tsv, write_register_xlsx


def test_title_row_gives_list_version(sample_register_xlsx: Path) -> None:
    register = read_register(sample_register_xlsx)

    assert register.version.list_date == date(2026, 10, 5)
    assert register.version.title == "Giltiga ramavtal 2026-10-05"


def test_every_data_row_is_read_with_its_excel_row_number(sample_register_xlsx: Path) -> None:
    register = read_register(sample_register_xlsx)

    assert len(register.rows) == 23
    first = register.rows[0]
    assert first.source_row == 3  # row 1 is the title, row 2 the headers
    assert first.values["Avtalsnummer"] == "23.3-1385-2025-011"
    assert first.values["Organisationsnummer"] == "5563372381      "  # not normalised here
    # Every cell in the real file is text: dates too, and an empty cell is "".
    assert first.values["Giltig från"] == "2026-07-01"
    assert first.values["Max förl. till"] == ""


def test_empty_rows_are_skipped(tmp_path: Path) -> None:
    rows = read_sample_tsv()[:3] + [[""] * 8] + read_sample_tsv()[3:4]

    register = read_register(write_register_xlsx(rows, tmp_path / "f.xlsx"))

    assert [row.source_row for row in register.rows] == [3, 5]


def test_title_without_date_is_rejected(tmp_path: Path) -> None:
    rows = [["Giltiga ramavtal"] * 8, *read_sample_tsv()[1:]]

    with pytest.raises(RegisterFormatError, match="no date"):
        read_register(write_register_xlsx(rows, tmp_path / "f.xlsx"))


def test_changed_headers_are_rejected(tmp_path: Path) -> None:
    rows = read_sample_tsv()
    rows[1] = ["Avtalsnr", *rows[1][1:]]

    with pytest.raises(RegisterFormatError, match="unexpected headers"):
        read_register(write_register_xlsx(rows, tmp_path / "f.xlsx"))
