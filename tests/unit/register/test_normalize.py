"""Tests for avtalsagent.register.normalize: raw rows to RegisterRow, with findings."""

from datetime import date, datetime
from pathlib import Path

from avtalsagent.register.normalize import (
    find_conflicts,
    find_foreign_org_numbers,
    normalize_row,
    normalize_rows,
)
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
    assert "unknown format" in result.rejected[0].message
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


def test_different_dates_per_sub_area_are_not_a_conflict() -> None:
    # Real case: 23.3-14537-2023-004 (Bemannia United AB) starts 2025-04-03 in one
    # sub-area and 2025-04-22 in another (Excel rows 334 and 341).
    rows = [
        normalize_row(raw(334, {"Giltig från": date(2025, 4, 3)})),
        normalize_row(
            raw(
                341,
                {
                    "Giltig från": date(2025, 4, 22),
                    "Delområde": "Möbler och inredning / Textila mattor / Direkttilldelning",
                },
            )
        ),
    ]

    assert find_conflicts(rows) == []


def test_same_agreement_with_two_org_numbers_is_found() -> None:
    rows = [normalize_row(raw(3)), normalize_row(raw(4, {"Organisationsnummer": "5563372381"}))]

    findings = find_conflicts(rows)

    assert [finding.source_row for finding in findings] == [4]
    assert "row 3 has 556839-3416" in findings[0].message


def test_same_agreement_and_sub_area_twice_is_found() -> None:
    rows = [normalize_row(raw(3)), normalize_row(raw(4, {"Giltig till": date(2028, 1, 1)}))]

    findings = find_conflicts(rows)

    assert [finding.source_row for finding in findings] == [4]
    assert "already on row 3" in findings[0].message


def test_procurement_in_two_framework_areas_is_accepted() -> None:
    # Real case: 23.3-2965-20 has agreements in two "Identifiering och behörighet" areas.
    rows = [
        normalize_row(raw(3)),
        normalize_row(raw(4, {"Avtalsnummer": "23.3-5834-2022-002", "Ramavtalsområde": "Annat"})),
    ]

    assert find_conflicts(rows) == []


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
