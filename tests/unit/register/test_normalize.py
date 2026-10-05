"""Tests for avtalsagent.register.normalize: raw rows to RegisterRow, with findings."""

from datetime import date, datetime
from pathlib import Path

from avtalsagent.register.normalize import find_conflicts, normalize_row, normalize_rows
from avtalsagent.register.read_excel import CellValue, RawRow, read_register

GOOD_ROW: dict[str, CellValue] = {
    "Avtalsnummer": "23.3-5834-2022-001",
    "Leverantör": "AB HOLMRIS B8 (f.d. Addentity Interiör AB)",
    "Organisationsnummer": "5568393416      ",
    "Ramavtalsområde": "Möbler och inredning",
    "Delområde": "Möbler och inredning / Textila mattor / Förnyad konkurrensutsättning",
    "Giltig från": date(2023, 11, 15),
    "Giltig till": date(2027, 11, 14),
    "Max förl. till": None,
}


def raw(source_row: int = 3, changes: dict[str, CellValue] | None = None) -> RawRow:
    return RawRow(source_row, {**GOOD_ROW, **(changes or {})})


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
    assert row.max_extension_to is None


def test_dates_as_text_or_datetime_are_accepted() -> None:
    row = normalize_row(
        raw(3, {"Giltig från": "2023-11-15", "Max förl. till": datetime(2029, 11, 14, 0, 0)})
    )

    assert row.valid_from == date(2023, 11, 15)
    assert row.max_extension_to == date(2029, 11, 14)


def test_org_number_stored_as_number_is_accepted() -> None:
    assert normalize_row(raw(3, {"Organisationsnummer": 5568393416})).org_number == "556839-3416"


def test_bad_rows_are_reported_not_dropped_silently() -> None:
    result = normalize_rows(
        [
            raw(3),
            raw(4, {"Organisationsnummer": "12345"}),
            raw(5, {"Giltig till": None}),
            raw(6, {"Giltig från": "15/11/2023"}),
        ]
    )

    assert [row.source_row for row in result.rows] == [3]
    assert [finding.source_row for finding in result.rejected] == [4, 5, 6]
    assert "10 digits" in result.rejected[0].message
    assert "Giltig till is empty" in result.rejected[1].message
    assert "not a date" in result.rejected[2].message


def test_one_org_number_can_have_several_supplier_names(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    names = {row.supplier_name for row in rows if row.org_number == "556337-2381"}

    assert names == {"2Home Hotel Gävle", "2Home Sthlm South"}


def test_sample_file_has_no_rejected_rows_or_conflicts(sample_register_xlsx: Path) -> None:
    result = normalize_rows(read_register(sample_register_xlsx).rows)

    assert len(result.rows) == 18
    assert result.rejected == []
    assert find_conflicts(result.rows) == []


def test_conflicting_dates_for_same_agreement_are_found() -> None:
    rows = [normalize_row(raw(3)), normalize_row(raw(4, {"Giltig till": date(2028, 1, 1)}))]

    findings = find_conflicts(rows)

    assert [finding.source_row for finding in findings] == [4]
    assert "differs from row 3" in findings[0].message


def test_procurement_with_two_framework_areas_is_found() -> None:
    rows = [
        normalize_row(raw(3)),
        normalize_row(raw(4, {"Avtalsnummer": "23.3-5834-2022-002", "Ramavtalsområde": "Annat"})),
    ]

    findings = find_conflicts(rows)

    assert [finding.source_row for finding in findings] == [4]
    assert "framework area" in findings[0].message
