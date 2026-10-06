"""Tests for avtalsagent.ingestion.extract.identifiers.

The lines are real, from the pilot files of the M4 survey (identifiers.md),
cited as sha[:12] §section or page. Lines whose source is not cited are made up.
"""

from avtalsagent.domain.extracted import Fact, FactKind, FactRole
from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.extract.identifiers import find_identifiers, role_of


def block(text: str, page: int | None = 1, kind: BlockKind = BlockKind.TEXT) -> Block:
    return Block(kind=kind, text=text, page=page)


def found(*texts: str) -> list[tuple[str, str, str, str | None]]:
    """(kind, value, rule, role) of the facts in blocks with these texts."""
    facts = find_identifiers([block(text) for text in texts])
    return [(f.kind.value, f.value, f.rule, f.role.value if f.role else None) for f in facts]


def test_case_number_in_a_page_header_is_the_documents_own() -> None:
    # 4b6c2a533fae p2: the header step 3 removes; the counter "(27)" is no citation.
    header = block("Sid 2 (27) Dnr 23.5-1688-2024", page=2, kind=BlockKind.PAGE_HEADER)

    assert find_identifiers([block("Vägledning", page=1), header]) == [
        Fact(
            kind=FactKind.PROCUREMENT_NUMBER,
            value="23.5-1688-2024",
            raw="23.5-1688-2024",
            rule="PROC",
            block=1,
            page=2,
            role=FactRole.SELF,
        )
    ]


def test_spellings_of_the_case_number_give_its_key() -> None:
    # 11db2f3d1852 §5.2 and the dot form of 2649 (319 hits).
    assert found("med diarienummer 23.3-8027.21 och är tillämpligt", "Dnr 23.3.2649-22") == [
        ("procurement_number", "23.3-8027-2021", "PROC", "self"),
        ("procurement_number", "23.3-2649-2022", "PROC", "self"),
    ]


def test_two_numbers_in_one_header_are_both_found() -> None:
    # 4f886a784c5d page header.
    assert [value for _, value, _, _ in found("Dnr 23.5-9343-2024 23.5-9345-2024")] == [
        "23.5-9343-2024",
        "23.5-9345-2024",
    ]


def test_agreement_number_gives_one_agreement_fact_only() -> None:
    # 14aa1cc8ee3d §pos0, the cover of a supplier card.
    assert found(
        "IT-konsulttjänster 2020 Dnr 23.3-2940-20 Ramavtal 23.3.2940-20:026 Experis AB"
    ) == [
        ("procurement_number", "23.3-2940-2020", "PROC", "self"),
        ("agreement_number", "23.3-2940-2020-026", "PROC", "self"),
    ]


def test_supplier_sequence_in_the_header_of_a_generic_file_is_kept() -> None:
    # 65d611d12eab p2: the generic main document carries supplier 001's number.
    assert found("23.3-8321-2024-001 IT-konsulttjänster - IT-säkerhet") == [
        ("agreement_number", "23.3-8321-2024-001", "PROC", "self")
    ]


def test_number_in_parentheses_is_a_citation() -> None:
    # 54211e718d8e §1.4: an earlier procurement.
    text = "ramavtal IT-konsulttjänster Resurskonsulter, region Södra (dnr 23.3-7067-17) som"
    assert found(text) == [("procurement_number", "23.3-7067-2017", "PROC", "citation")]


def test_number_in_running_text_is_the_documents_own() -> None:
    # adcd1c5ed90e §2: the call-off template says which agreement it belongs to.
    assert found(
        "Detta avrop görs från ramavtal",
        "Programvaror och tjänster – Informationsförsörjning",
        "med",
        "diarienummer 23.3-2283-22",
    ) == [("procurement_number", "23.3-2283-2022", "PROC", "self")]


def test_role_needs_both_parentheses() -> None:
    text = "Ramavtal (se 23.3-2940-20 och bilagor"
    start = text.index("23.3")
    assert role_of(text, start, start + len("23.3-2940-20")) is FactRole.SELF


def test_section_numbers_and_numbers_without_a_year_are_no_case_numbers() -> None:
    # 20c753d88340 §94 has no year; "21.3.12.20" has the shape of a section number.
    assert found("Dnr 23.3.10150, har ett ansvarstak införts", "enligt punkt 21.3.12.20") == []


def test_old_agreement_number_after_a_label() -> None:
    # fb9447f0b8bf §1.
    assert found("kompletteras Volymavtalet för Programvaror, avtalsnummer 6765/05 enligt") == [
        ("procurement_number", "6765/05", "OLD", "self")
    ]


def test_old_form_without_a_label_is_not_a_number() -> None:
    # 50edddbad6c7 block 2552: a government bill has the same shape.
    assert found("I förarbetena till LOU (prop. 2015/16:195 s. 435-436) framgår") == []


def test_letterhead_case_number() -> None:
    # fb9447f0b8bf §pos0: the letterhead of the IBM amendment.
    letterhead = "DIARIENR\n2018-05-23\nERT DATUM\n96-15-2015\nER BETECKNING\nAvtal 6765/05"
    assert found(letterhead) == [
        ("procurement_number", "6765/05", "OLD", "self"),
        ("procurement_number", "96-15-2015", "OLDKK", "self"),
    ]


def test_org_numbers_with_a_right_check_digit() -> None:
    # A page footer, and 087f9c5a2f56 §pos0 with a phone number of the same shape.
    assert found("Organisationsnummer 202100-0829", "Kammarkollegiet +4687000800") == [
        ("org_number", "202100-0829", "ORG", None)
    ]


def test_unfilled_agreement_sequence_gives_the_procurement_and_one_placeholder() -> None:
    # 0692da436391 §1.2.1, the generic main document of 1688.
    assert found("Ramavtal med avtalsnummer 23.3-1688-2024:[XXX], har träffats") == [
        ("procurement_number", "23.3-1688-2024", "PROC", "self"),
        ("placeholder", "agreement_number", "PH", None),
    ]


def test_case_number_typo_is_a_placeholder_not_a_number() -> None:
    # 5d6e948959cc §5.2.1: a five-digit year and an unfilled sequence.
    assert found("Ramavtal med avtalsnummer 23.3-5890-20263-XXX, har träffats") == [
        ("placeholder", "agreement_number", "PH", None)
    ]


def test_labelled_placeholders() -> None:
    assert found(
        # 0692da436391 §1.2.1
        "och Leverantör organisationsnummer XXXXXX-XXXX, nedan Ramavtalsleverantören.",
        # 11db2f3d1852 block 698
        "Ramavtal med avtalsnummer [X], har träffats för Avropsberättigades räkning",
        # 145e34c51489 §1: in brackets, found once
        "8. Avropsförfrågan [diarienr: xxxx] med bilagor",
        # 3b22b96ca023 §2: the field in the next cell, found once
        "Organisationsnummer: | [Avropsberättigades organisationsnummer]\nPart: | [Leverantör X]",
        # 0486216326ec §2: an empty table cell after the label
        "E-postadress: | \nOrganisationsnummer: | \nPersonuppgiftsbiträde: | ",
    ) == [
        ("placeholder", "org_number", "PH", None),
        ("placeholder", "agreement_number", "PH", None),
        ("placeholder", "procurement_number", "PH", None),
        ("placeholder", "org_number", "PH", None),
        ("placeholder", "org_number", "PH", None),
    ]


def test_label_without_a_value_in_a_sentence_is_no_placeholder() -> None:
    assert found(
        "Leverantören ska ange sitt organisationsnummer.",
        "Organisationsnummer:, se ovan",
        "Organisationsnummer: | 556866-4444",  # a filled table cell
        "och Exempel AB, organisationsnummer 556866-4444, nedan Ramavtalsleverantören.",
    ) == [("org_number", "556866-4444", "ORG", None), ("org_number", "556866-4444", "ORG", None)]
