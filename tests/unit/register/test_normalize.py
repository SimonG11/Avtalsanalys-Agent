"""Tests for avtalsagent.register.normalize: raw rows to RegisterRow, with findings."""

from datetime import date, datetime
from pathlib import Path

from avtalsagent.register.normalize import (
    find_conflicts,
    find_foreign_org_numbers,
    normalize_row,
    normalize_rows,
)
from avtalsagent.register.read_excel import EXPECTED_HEADERS, CellValue, RawRow, read_register

# Excel row 16, as the real file stores it: every cell is text, an empty cell is "".
GOOD_ROW: dict[str, CellValue] = {
    "Avtalsnummer": "23.3-5834-2022-001",
    "Leverantör": "AB HOLMRIS B8 (f.d. Addentity Interiör AB)",
    "Organisationsnummer": "5568393416      ",
    "Ramavtalsområde": "Möbler och inredning",
    "Delområde": "Möbler och inredning / Textila mattor / Förnyad konkurrensutsättning",
    "Giltig från": "2023-11-15",
    "Giltig till": "2027-11-14",
    "Max förl. till": "",
}


def raw(source_row: int = 3, changes: dict[str, CellValue] | None = None) -> RawRow:
    return RawRow(source_row, {**GOOD_ROW, **(changes or {})})


def excel_row(source_row: int, *cells: str) -> RawRow:
    """A row of the real file, with its cells in column order."""
    return RawRow(source_row, dict(zip(EXPECTED_HEADERS, cells, strict=True)))


def test_row_is_normalised() -> None:
    row = normalize_row(raw())

    assert row.agreement_number == "23.3-5834-2022-001"
    assert row.procurement_number == "23.3-5834-2022"
    assert row.sequence == "001"
    assert row.supplier_name == "AB HOLMRIS B8"
    assert row.former_supplier_name == "Addentity Interiör AB"
    assert row.org_number == "556839-3416"
    assert row.sub_area_path == (
        "Möbler och inredning",
        "Textila mattor",
        "Förnyad konkurrensutsättning",
    )
    assert row.valid_from == date(2023, 11, 15)
    assert row.max_extension_to is None  # "" in the file


def test_excel_dates_and_cells_without_value_are_accepted() -> None:
    row = normalize_row(
        raw(3, {"Giltig från": date(2023, 11, 15), "Giltig till": datetime(2027, 11, 14, 0, 0)})
    )
    empty = normalize_row(raw(3, {"Max förl. till": None}))

    assert (row.valid_from, row.valid_to) == (date(2023, 11, 15), date(2027, 11, 14))
    assert empty.max_extension_to is None


def test_org_number_stored_as_number_is_accepted() -> None:
    assert normalize_row(raw(3, {"Organisationsnummer": 5568393416})).org_number == "556839-3416"


def test_bad_rows_are_reported_not_dropped_silently() -> None:
    result = normalize_rows(
        [
            raw(3),
            raw(4, {"Organisationsnummer": "12345"}),
            raw(5, {"Giltig till": ""}),
            raw(6, {"Giltig från": "15/11/2023"}),
        ]
    )

    assert [row.source_row for row in result.rows] == [3]
    assert [finding.source_row for finding in result.rejected] == [4, 5, 6]
    assert "unknown format" in result.rejected[0].message
    assert "Giltig till is empty" in result.rejected[1].message
    assert "not a date" in result.rejected[2].message


def test_one_org_number_can_have_several_supplier_names(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    names = {row.supplier_name for row in rows if row.org_number == "556337-2381"}

    assert names == {"2Home Hotel Gävle", "2Home Sthlm South"}


def test_sample_file_has_no_rejected_rows_or_conflicts(sample_register_xlsx: Path) -> None:
    result = normalize_rows(read_register(sample_register_xlsx).rows)

    assert len(result.rows) == 23
    assert result.rejected == []
    assert find_conflicts(result.rows) == []


def test_filled_max_extension_becomes_a_date(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    # In the sample only AB Svenska Pass (Excel rows 24 and 25) has "Max förl. till" filled.
    extended = {row.source_row: row.max_extension_to for row in rows if row.max_extension_to}

    assert extended == {24: date(2028, 3, 22), 25: date(2028, 3, 22)}


def test_different_dates_per_sub_area_are_not_a_conflict() -> None:
    # Excel rows 334 and 341: agreement 23.3-14537-2023-004 (Bemannia United AB)
    # starts 2025-04-03 in one sub-area and 2025-04-22 in another.
    rows = [
        normalize_row(
            excel_row(
                334,
                "23.3-14537-2023-004",
                "Bemannia United AB",
                "5592895709      ",
                "Bemanningstjänster",
                "Bemanningstjänster / Bemanningstjänster - IT-tjänster överstigande 1000 timmar"
                " / Östra Mellansverige",
                "2025-04-03",
                "2029-04-02",
                "",
            )
        ),
        normalize_row(
            excel_row(
                341,
                "23.3-14537-2023-004",
                "Bemannia United AB",
                "5592895709      ",
                "Bemanningstjänster",
                "Bemanningstjänster / Bemanningstjänster - Kontorstjänster överstigande 1000"
                " timmar / Västsverige",
                "2025-04-22",
                "2029-04-21",
                "",
            )
        ),
    ]

    assert [(row.valid_from, row.valid_to) for row in rows] == [
        (date(2025, 4, 3), date(2029, 4, 2)),
        (date(2025, 4, 22), date(2029, 4, 21)),
    ]
    assert find_conflicts(rows) == []


def test_same_agreement_with_two_org_numbers_is_found() -> None:
    rows = [normalize_row(raw(3)), normalize_row(raw(4, {"Organisationsnummer": "5563372381"}))]

    findings = find_conflicts(rows)

    assert [finding.source_row for finding in findings] == [4]
    assert "row 3 has 556839-3416" in findings[0].message


def test_same_agreement_and_sub_area_twice_is_found() -> None:
    rows = [normalize_row(raw(3)), normalize_row(raw(4, {"Giltig till": "2028-01-01"}))]

    findings = find_conflicts(rows)

    assert [finding.source_row for finding in findings] == [4]
    assert "already on row 3" in findings[0].message


def test_procurement_in_two_framework_areas_is_accepted(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    # Excel rows 24 and 25: agreement 23.3-2965-20:001 (AB Svenska Pass) is in both
    # "Identifiering och behörighet" framework areas.
    svenska_pass = [row for row in rows if row.procurement_number == "23.3-2965-20"]

    assert [(row.source_row, row.agreement_number, row.framework_area) for row in svenska_pass] == [
        (24, "23.3-2965-20:001", "Identifiering och behörighet - förnyad konkurrensutsättning"),
        (25, "23.3-2965-20:001", "Identifiering och behörighet - särskild fördelningsnyckel"),
    ]
    assert find_conflicts(svenska_pass) == []


def test_foreign_org_numbers_are_listed_once_per_supplier() -> None:
    martela: dict[str, CellValue] = {
        "Leverantör": "Martela Oyj",
        "Organisationsnummer": "FI01148912      ",
    }
    rows = [normalize_row(raw(3)), normalize_row(raw(4, martela)), normalize_row(raw(5, martela))]

    findings = find_foreign_org_numbers(rows)

    assert [(f.source_row, f.message) for f in findings] == [
        (4, "foreign org number FI01148912 (Martela Oyj)")
    ]
