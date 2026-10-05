"""Tests for avtalsagent.domain.identifiers: one test per normalisation rule.

The examples are real values from "Alla giltiga ramavtal" (2026-10-05).
"""

import pytest

from avtalsagent.domain.identifiers import (
    IdentifierError,
    is_swedish_org_number,
    normalize_org_number,
    parse_agreement_number,
    parse_supplier_name,
    split_sub_area,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5563372381      ", "556337-2381"),  # Excel form: no hyphen, trailing spaces
        ("556337-2381", "556337-2381"),  # PDF form: already normalised
        (" 5591999601", "559199-9601"),
    ],
)
def test_org_number_is_normalised(raw: str, expected: str) -> None:
    assert normalize_org_number(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FI01148912      ", "FI01148912"),  # Martela Oyj, Finland
        ("CVR:37120928    ", "CVR:37120928"),  # Stibo Complete A/S, Denmark
        ("33948786        ", "33948786"),  # Huuray A/S, Danish CVR without prefix
        ("965920358       ", "965920358"),  # Norwegian Air Shuttle ASA, Norway
        ("FC16134         ", "FC16134"),  # EBSCO International, Inc., UK register
    ],
)
def test_foreign_org_number_is_kept_as_written(raw: str, expected: str) -> None:
    normalized = normalize_org_number(raw)

    assert normalized == expected
    assert not is_swedish_org_number(normalized)


def test_swedish_org_number_is_recognised() -> None:
    assert is_swedish_org_number(normalize_org_number("5563372381"))


@pytest.mark.parametrize("raw", ["", "12345", "556337-238X", "abc", "SE 55-63"])
def test_org_number_with_unknown_format_is_rejected(raw: str) -> None:
    with pytest.raises(IdentifierError):
        normalize_org_number(raw)


def test_agreement_number_is_split_into_procurement_and_sequence() -> None:
    number = parse_agreement_number("23.3-14537-2023-001")

    assert number.procurement_number == "23.3-14537-2023"
    assert number.sequence == "001"
    assert number.full == "23.3-14537-2023-001"


def test_agreement_numbers_in_same_procurement_share_procurement_number() -> None:
    first = parse_agreement_number("23.3-5834-2022-001")
    other = parse_agreement_number("23.3-5834-2022-042")

    assert first.procurement_number == other.procurement_number == "23.3-5834-2022"


@pytest.mark.parametrize(
    ("raw", "procurement", "sequence"),
    [
        ("23.3-2965-20:001", "23.3-2965-20", "001"),  # older form, two-digit year and colon
        ("23.3-4613-2023-003-A", "23.3-4613-2023", "003-A"),  # variant of agreement 003
        ("23.5-3718-2024", "23.5-3718-2024", None),  # Microsoft: only a case number
        ("6765/05", "6765/05", None),  # IBM: old case number
    ],
)
def test_other_agreement_number_formats_in_the_list(
    raw: str, procurement: str, sequence: str | None
) -> None:
    number = parse_agreement_number(raw)

    assert (number.full, number.procurement_number, number.sequence) == (
        raw,
        procurement,
        sequence,
    )


@pytest.mark.parametrize("raw", ["", "abc-001", "23.3-14537-23-001", "23.3-14537-2023-001-AB"])
def test_unknown_agreement_number_format_is_rejected(raw: str) -> None:
    with pytest.raises(IdentifierError):
        parse_agreement_number(raw)


def test_former_name_is_split_from_supplier_name() -> None:
    supplier = parse_supplier_name("AB HOLMRIS B8 (f.d. Addentity Interiör AB)")

    assert supplier.name == "AB HOLMRIS B8"
    assert supplier.former_name == "Addentity Interiör AB"


def test_supplier_name_without_former_name() -> None:
    supplier = parse_supplier_name("  2Home  Hotel Gävle ")

    assert supplier.name == "2Home Hotel Gävle"
    assert supplier.former_name is None


def test_empty_supplier_name_is_rejected() -> None:
    with pytest.raises(IdentifierError):
        parse_supplier_name("   ")


def test_sub_area_is_split_into_levels() -> None:
    assert split_sub_area("Gävleborgs län / Gävle / Gävle zon 1 - Longstay") == (
        "Gävleborgs län",
        "Gävle",
        "Gävle zon 1 - Longstay",
    )


def test_hyphen_inside_a_level_is_not_a_separator() -> None:
    levels = split_sub_area(
        "Bemanningstjänster / Bemanningstjänster - IT-tjänster upp till 1000 timmar / Stockholm"
    )

    assert levels[1] == "Bemanningstjänster - IT-tjänster upp till 1000 timmar"


def test_sub_area_with_empty_level_is_rejected() -> None:
    with pytest.raises(IdentifierError):
        split_sub_area("Möbler och inredning /  / Utemöbler")
