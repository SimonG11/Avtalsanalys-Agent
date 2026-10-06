"""Tests for avtalsagent.domain.identifiers: one test per normalisation rule.

The examples are real values from "Alla giltiga ramavtal" (2026-10-05) and, for
the case numbers and organisation numbers in documents, from the pilot files of
the M4 survey (identifiers.md §1, §9), cited as sha[:12] §section.
"""

from pathlib import Path

import pytest

from avtalsagent.domain.identifiers import (
    IdentifierError,
    agreement_key,
    find_org_numbers,
    is_placeholder,
    is_swedish_org_number,
    luhn_valid,
    normalize_org_number,
    parse_agreement_number,
    parse_agreement_reference,
    parse_procurement_number,
    parse_supplier_name,
    procurement_key,
    split_sub_area,
)
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register

REGISTER_FILES = sorted((Path(__file__).parents[3] / "data" / "register").glob("*.xlsx"))


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


def test_swedish_vat_number_gives_the_org_number() -> None:
    # A VAT number is "SE", the org number and "01"; it is no foreign number.
    assert normalize_org_number("SE202100082901") == "202100-0829"
    assert normalize_org_number("SE 202100-0829 01") == "202100-0829"


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


# --- Case numbers, agreement numbers and organisation numbers in the documents ---


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("23.3-5890-2023", "23.3-5890-2023"),  # the register's spelling
        ("23.3-2940-20", "23.3-2940-2020"),  # two-digit year, also in the register
        ("23.3.2940-20", "23.3-2940-2020"),  # dot after the series (615 hits)
        ("23.3-8027-21", "23.3-8027-2021"),  # two-digit year where the register has four
        ("23.3-8027.21", "23.3-8027-2021"),  # dot before the year, 11db2f3d1852 §5.2
        ("23.3–1688–2024", "23.3-1688-2024"),  # en dashes
        ("23.5-03893-2021", "23.5-3893-2021"),  # leading zero, 21d3f9cdf880 page header
        ("6765/05", "6765/05"),  # IBM, the old form
        ("96-15-2015", "96-15-2015"),  # Kammarkollegiet's letterhead, fb9447f0b8bf
    ],
)
def test_procurement_number_spellings_give_one_key(raw: str, key: str) -> None:
    assert parse_procurement_number(raw).key == key
    assert procurement_key(key) == key  # the key of a key is the key


@pytest.mark.parametrize(
    "raw",
    [
        "23.3.10150",  # no year: 20c753d88340 §94 "Dnr 23.3.10150"
        "23.3-5890-20263",  # five-digit year: 5d6e948959cc §5.2.1 "23.3-5890-20263-XXX"
        "21.3.12.20",  # both separators dots: a section number
        "23.3-2940-20:018",  # an agreement number, not a procurement number
        "",
    ],
)
def test_procurement_number_without_a_usable_year_is_refused(raw: str) -> None:
    with pytest.raises(IdentifierError):
        parse_procurement_number(raw)
    assert procurement_key(raw) is None


def test_series_is_never_crossed() -> None:
    # 23.5-1688-2024 is the agreement-management case of procurement 23.3-1688-2024
    # (4b6c2a533fae page header "Sid 2 (27) Dnr 23.5-1688-2024").
    assert procurement_key("23.5-1688-2024") != procurement_key("23.3-1688-2024")


@pytest.mark.parametrize(
    ("raw", "case_management"),
    [
        ("23.5-9343-2024", True),  # agreement management, 4f886a784c5d headers
        ("96-15-2015", True),  # the letterhead's own case
        ("23.3-1688-2024", False),
        ("6765/05", False),
    ],
)
def test_case_management_numbers(raw: str, case_management: bool) -> None:
    assert parse_procurement_number(raw).is_case_management is case_management


@pytest.mark.parametrize(
    ("raw", "procurement", "sequence"),
    [
        ("23.3.2940-20:026", "23.3-2940-2020", "026"),  # 14aa1cc8ee3d cover
        ("23.3.2649-22-003", "23.3-2649-2022", "003"),  # 185872a6bb90 §1.3.1
        ("23.3-4613-2023-003-A", "23.3-4613-2023", "003-A"),  # register
        ("23.5-3718-2024", "23.5-3718-2024", None),  # Microsoft: no sequence
        ("6765/05", "6765/05", None),  # IBM: no sequence
    ],
)
def test_agreement_reference_is_split_into_procurement_and_sequence(
    raw: str, procurement: str, sequence: str | None
) -> None:
    reference = parse_agreement_reference(raw)

    assert (reference.procurement.key, reference.sequence, reference.raw) == (
        procurement,
        sequence,
        raw,
    )


@pytest.mark.parametrize(
    ("document", "register"),
    [
        ("23.3.2940-20:018", "23.3-2940-20:018"),
        ("23.3.2649-22-003", "23.3-2649-2022-003"),
    ],
)
def test_agreement_spellings_of_document_and_register_give_one_key(
    document: str, register: str
) -> None:
    assert agreement_key(document) == agreement_key(register) is not None


def test_agreement_without_sequence_keys_to_its_procurement() -> None:
    assert agreement_key("23.5-3718-2024") == procurement_key("23.5-3718-2024")
    assert agreement_key("6765/05") == procurement_key("6765/05") == "6765/05"


def test_agreement_key_is_idempotent_and_none_for_garbage() -> None:
    key = agreement_key("23.3.2940-20:018")
    assert key == "23.3-2940-2020-018"
    assert agreement_key(key) == key
    assert agreement_key("23.3-5890-20263-XXX") is None


# One real value for every shape of number in the register (2026-10-05).
REGISTER_SHAPES = [
    ("23.3-56-2023-124", "23.3-56-2023"),
    ("23.3-265-2024-010", "23.3-265-2024"),
    ("23.3-1385-2025-011", "23.3-1385-2025"),
    ("23.3-15030-2024-013", "23.3-15030-2024"),
    ("23.3-4613-2023-003-A", "23.3-4613-2023"),
    ("23.3-2965-20:001", "23.3-2965-20"),
    ("23.3-12000-2020-01", "23.3-12000-2020"),
    ("23.3-1200020-20:005", "23.3-1200020-20"),
    ("23.5-3718-2024", "23.5-3718-2024"),
    ("6765/05", "6765/05"),
]


@pytest.mark.parametrize(("agreement", "procurement"), REGISTER_SHAPES)
def test_every_register_shape_keys(agreement: str, procurement: str) -> None:
    reference = parse_agreement_reference(agreement)

    assert reference.procurement.key == procurement_key(procurement)
    assert agreement_key(agreement) is not None


@pytest.mark.skipif(not REGISTER_FILES, reason="the register is not downloaded (data/register)")
def test_every_number_of_the_real_register_keys() -> None:
    rows = normalize_rows(read_register(REGISTER_FILES[-1]).rows).rows
    procurements = {row.procurement_number for row in rows}
    agreements = {row.agreement_number for row in rows}

    assert all(procurement_key(number) for number in procurements)
    assert all(agreement_key(number) for number in agreements)
    # No two register numbers share a key: keying loses nothing.
    assert len({procurement_key(number) for number in procurements}) == len(procurements)
    assert len({agreement_key(number) for number in agreements}) == len(agreements)


@pytest.mark.parametrize(
    ("org_number", "valid"),
    [
        ("202100-0829", True),  # Kammarkollegiet
        ("2021000829", True),
        ("556866-4444", True),  # ÅF Digital Solutions AB, 7a49e1a61b31 §9.1.1
        ("202100-0828", False),  # one digit off
        ("468700-0800", False),  # the phone number +4687000800, 087f9c5a2f56
        ("55686-4444", False),
    ],
)
def test_luhn_check_digit(org_number: str, valid: bool) -> None:
    assert luhn_valid(org_number) is valid


def test_org_numbers_are_found_in_text() -> None:
    # fb9447f0b8bf §3: two parties on one line; 10 hits in the pilot have no hyphen.
    text = "IBM Svenska AB Org nr: 202100-0829 Org nr: 556026-6883 Org.nr 2021000829"

    assert [org for _, _, org in find_org_numbers(text)] == [
        "202100-0829",
        "556026-6883",
        "202100-0829",
    ]


def test_phone_numbers_with_the_shape_of_an_org_number_are_not_found() -> None:
    # 087f9c5a2f56 §pos0: "Statens inköpscentral vid Kammarkollegiet ⏎ +4687000800"
    assert find_org_numbers("Kammarkollegiet +4687000800 eller 08-700 08 00") == []


def test_number_after_plus_is_a_phone_number_even_with_a_right_check_digit() -> None:
    # Phone numbers fail the check digit by chance only: +4687000804 passes it.
    assert find_org_numbers("Telefon +4687000804") == []
    assert find_org_numbers("Telefon 4687000804") == [(8, 18, "468700-0804")]


def test_wrong_check_digit_is_not_an_org_number() -> None:
    assert find_org_numbers("organisationsnummer 202100-0828") == []


def test_vat_number_gives_its_org_number_once() -> None:
    assert find_org_numbers("Momsreg.nr SE 202100-0829 01") == [(11, 28, "202100-0829")]


def test_nuts_codes_are_not_vat_numbers() -> None:
    # 19c85c74c3b2 §pos1: "NUTS 3 Län … SE121 Uppsala ⏎ SE12 ⏎ SE122 Södermanlands"
    assert find_org_numbers("SE121 Uppsala SE12 SE122 Södermanlands län") == []


@pytest.mark.parametrize(
    "raw",
    [
        "XXXXXX-XXXX",  # 0692da436391 §1.2.1
        "[xxxxxx-yyyy]",  # e3a24695fe04 §9.1.1
        "[X]",  # "Ramavtal med avtalsnummer [X]"
        "XX",
        "23.3-5890-20263-XXX",  # 5d6e948959cc §5.2.1
        "[Avropsberättigades organisationsnummer]",
        "Klicka här för att ange text.",  # a Word content control
        " | ",  # an empty table cell
    ],
)
def test_placeholders_are_recognised(raw: str) -> None:
    assert is_placeholder(raw)


@pytest.mark.parametrize("raw", ["556866-4444", "Exxon Mobil AB", "[1]", "23.3-1688-2024"])
def test_filled_values_are_not_placeholders(raw: str) -> None:
    assert not is_placeholder(raw)
