"""Tests for avtalsagent.validation.register_facts: the register-facts rule as a pure function.

Each test builds a draft as the model would hand it in, the register rows a
reader would give for its declared agreements (public values from the
register of 2026-10-05: agreement numbers, suppliers and their organisation
numbers), and the sections and user texts that may back a value, and checks
the problems the model is told about, the rows the user gets and the values
a reservation names. The register answers in gold_sv.jsonl (q10-q13) pass.
"""

import time
from collections.abc import Sequence
from datetime import date

import pytest

from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.schemas import FinalAnswer, RegisterFact
from avtalsagent.agent.sections import CitedSection
from avtalsagent.domain.identifiers import agreement_key
from avtalsagent.validation.register_facts import RegisterReport, check_register_facts

TODAY = date(2026, 10, 7)


def row(
    agreement_number: str,
    supplier_name: str,
    org_number: str,
    sub_area: str,
    valid_from: date,
    valid_to: date,
    *,
    former_names: Sequence[str] = (),
    max_extension_to: date | None = None,
) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=agreement_number,
        procurement_number=agreement_number.rpartition("-")[0],
        supplier_name=supplier_name,
        org_number=org_number,
        former_names=list(former_names),
        framework_area=sub_area.split(" / ")[0],
        sub_area=sub_area,
        valid_from=valid_from,
        valid_to=valid_to,
        max_extension_to=max_extension_to,
    )


ADVANCE = row(
    "23.3-5890-2023-002",
    "Nordlo Advance AB",
    "556486-1689",
    "IT-drift / IT-drift Mindre, upp till 200 anställda",
    date(2024, 11, 14),
    date(2028, 11, 13),
    former_names=["EPM Data"],
)
# Nordlo Improve AB: another company, another organisation number, the same dates.
IMPROVE = row(
    "23.3-10639-2023-007",
    "Nordlo Improve AB",
    "556271-9129",
    "IT-drift / IT-drift Större, fler än 200 anställda",
    date(2024, 11, 14),
    date(2028, 11, 13),
)
TELIA = row(
    "23.3-8027-2021-004",
    "Telia Cygate AB",
    "556549-8952",
    "Programvaror och tjänster / Programvarulösningar",
    date(2023, 2, 18),
    date(2027, 2, 17),
)
IT_SECURITY = [
    row(
        f"23.3-8321-2024-00{sequence}",
        supplier,
        org_number,
        "IT-konsulttjänster Resurskonsulter / IT-konsulttjänster 3. IT-säkerhet",
        date(2026, 3, 10),
        date(2030, 3, 9),
    )
    for sequence, supplier, org_number in [
        (1, "Castra Group AB", "556958-4401"),
        (2, "Chas Visual Management AB", "556726-4758"),
        (3, "Combitech Aktiebolag", "556218-6790"),
        (4, "HiQ International AB", "556529-3205"),
        (5, "Knowit Aktiebolag", "556391-0354"),
        (6, "Nexer AB", "556451-9345"),
        (7, "Pro4u AB", "556590-6897"),
        (8, "Regent AB", "556971-2499"),
    ]
]
HELPDESK = [
    row(
        f"23.3-14537-2023-{sequence}",
        supplier,
        org_number,
        f"Bemanningstjänster / Bemanningstjänster - IT-tjänster {hours} / Övre Norrland",
        date(2025, 4, 3),
        date(2029, 4, 2),
    )
    for sequence, supplier, org_number in [
        ("002", "Academic Work Sweden AB", "556559-5450"),
        ("005", "Eccera Professionals AB", "556718-3693"),
        ("006", "Clockwork Bemanning & Rekrytering AB", "556913-7325"),
        ("012", "OnePartner Group AB", "556831-2812"),
        ("013", "Perido AB", "556639-6387"),
        ("014", "Poolia AB", "556426-7655"),
        ("015", "Randstad AB", "556242-1718"),
    ]
    for hours in ["upp till 1000 timmar", "överstigande 1000 timmar"]
]
# The register writes this agreement two ways, on different rows.
DIGITAL_INTERPRETATIONS = [
    row(
        number,
        "Digital Interpretations Scandinavia AB",
        "559032-5394",
        f"Tolkförmedlingstjänster / {county}",
        date(2023, 2, 15),
        date(2027, 2, 14),
    )
    for number, county in [
        ("23.3-12000-2020-001", "Stockholms län"),
        ("23.3-12000-2020-01", "Örebro län"),
    ]
]
REGISTER = [ADVANCE, IMPROVE, TELIA, *IT_SECURITY, *HELPDESK, *DIGITAL_INTERPRETATIONS]

# The answers of gold_sv.jsonl, word for word.
Q10 = (
    "Avtal 23.3-5890-2023-002, organisationsnummer 556486-1689. Registret anger tidigare namn "
    "EPM Data. Avtalet gäller 2024-11-14–2028-11-13."
)
Q11 = (
    "Sju leverantörer (yrkesområde IT-tjänster, delområde Övre Norrland, samma för uppdrag upp "
    "till och över 1000 timmar): Academic Work Sweden AB (23.3-14537-2023-002), Eccera "
    "Professionals AB (-005), Clockwork Bemanning & Rekrytering AB (-006), OnePartner Group AB "
    "(-012), Perido AB (-013), Poolia AB (-014) och Randstad AB (-015)."
)
Q12 = (
    "Åtta leverantörer: Castra Group AB, Chas Visual Management AB, Combitech Aktiebolag, HiQ "
    "International AB, Knowit Aktiebolag, Nexer AB, Pro4u AB och Regent AB (avtal "
    "23.3-8321-2024-001 till -008). Alla avtal gäller 2026-03-10 – 2030-03-09."
)
Q13 = "Avtal 23.3-8027-2021-004, organisationsnummer 556549-8952, giltigt 2023-02-18 – 2027-02-17."
Q24 = (
    "Iver Sverige AB, 585 kr per timme exklusive moms (priser från 2026-09-01). Därefter kommer "
    "Videnca AB (733) och Orange Business Digital Sweden AB (744)."
)
PRICE_LIST = (
    "IT-drift Större | Samtliga Takpriser är pris per timme i SEK exklusive mervärdesskatt. / "
    "Nya priser från 2026-09-01 | Org.nr. | Iver Sverige AB | 556575-3042 | 585"
)
WRONG_DATE = (
    "Datumet 2028-11-14 står inte i registret för 23.3-5890-2023-002 (giltigt "
    "2024-11-14–2028-11-13) och inte i något citerat avsnitt. Rätta det, eller räkna det med "
    "calculate_date och skriv dess step i meningen."
)
IMPROVES_ORG_NUMBER = (
    "Organisationsnumret 556271-9129 hör inte till 23.3-5890-2023-002 (registret: "
    "556486-1689). Kopiera rätt nummer från search_register."
)


def check(
    text: str,
    declared: Sequence[str],
    *,
    sections: Sequence[CitedSection] = (),
    user_texts: Sequence[str] = (),
) -> RegisterReport:
    """The rule on a draft, with each declared agreement's rows as a reader gives them."""
    draft = FinalAnswer(answered=True, text=text, register_facts=list(declared))
    entries = {number: rows_of(number) for number in declared}
    return check_register_facts(draft, entries, sections, user_texts, TODAY)


def rows_of(number: str) -> list[RegisterEntry] | None:
    key = agreement_key(number)
    rows = [entry for entry in REGISTER if agreement_key(entry.agreement_number) == key]
    return rows if key is not None and rows else None


def section(text: str, title: str = "Konsultpriser") -> CitedSection:
    return CitedSection(
        sha256="4f" * 32,
        section_position=12,
        section_number="2.7.3",
        section_title=title,
        file_title="Vägledning - IT-drift",
        page_titles=["IT-drift Större, fler än 200 anställda"],
        page_start=10,
        text=text,
    )


def fact(entry: RegisterEntry) -> RegisterFact:
    return RegisterFact(
        agreement_number=entry.agreement_number,
        supplier_name=entry.supplier_name,
        former_names=entry.former_names,
        org_number=entry.org_number,
        sub_area=entry.sub_area,
        valid_from=entry.valid_from,
        valid_to=entry.valid_to,
        max_extension_to=entry.max_extension_to,
    )


def numbers(rows: Sequence[RegisterEntry]) -> list[str]:
    return list(dict.fromkeys(entry.agreement_number for entry in rows))


# --- the gold answers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "declared"),
    [
        (Q10, ["23.3-5890-2023-002"]),
        (Q11, numbers(HELPDESK)),
        (Q12, numbers(IT_SECURITY)),
        (Q13, ["23.3-8027-2021-004"]),
    ],
    ids=["q10", "q11", "q12", "q13"],
)
def test_the_gold_register_answers_pass(text: str, declared: list[str]) -> None:
    report = check(text, declared)

    assert report.problems == []
    assert report.unbacked == []


def test_the_facts_are_every_row_of_each_declared_agreement_in_declared_order() -> None:
    text = "Telia Cygate AB har avtal 23.3-8027-2021-004 och Eccera Professionals AB har -005."

    report = check(text, ["23.3-8027-2021-004", "23.3-14537-2023-005"])

    eccera = [entry for entry in HELPDESK if entry.agreement_number == "23.3-14537-2023-005"]
    assert len(eccera) == 2
    assert report.facts == [fact(TELIA), *(fact(entry) for entry in eccera)]


def test_a_document_answer_without_register_values_passes_with_no_facts() -> None:
    report = check("Uppsägningstiden är tre (3) månader [1].", [])

    assert report == RegisterReport(problems=[], facts=[], unbacked=[])


# --- wrong values --------------------------------------------------------------------


def test_a_wrong_date_is_a_problem_that_names_the_registers_dates() -> None:
    report = check(Q10.replace("2028-11-13", "2028-11-14"), ["23.3-5890-2023-002"])

    assert report.problems == [WRONG_DATE]
    assert report.unbacked == ["2028-11-14"]
    assert report.facts == [fact(ADVANCE)]


def test_an_org_number_with_one_wrong_digit_has_the_wrong_check_digit() -> None:
    report = check(Q10.replace("556486-1689", "556486-1688"), ["23.3-5890-2023-002"])

    assert report.problems == [
        "Organisationsnumret 556486-1688 har fel kontrollsiffra. Kopiera det från search_register."
    ]
    assert report.unbacked == ["556486-1688"]


@pytest.mark.parametrize(
    ("vat_number", "problems"),
    [
        ("SE556486168901", []),
        (
            "SE556486168801",
            [
                "Organisationsnumret SE556486168801 har fel kontrollsiffra. Kopiera det från "
                "search_register."
            ],
        ),
    ],
    ids=["right", "one wrong digit"],
)
def test_a_vat_number_is_read_as_its_org_number(vat_number: str, problems: list[str]) -> None:
    text = f"Nordlo Advance AB har avtal 23.3-5890-2023-002 och momsnummer {vat_number}."

    assert check(text, ["23.3-5890-2023-002"]).problems == problems


def test_a_non_breaking_hyphen_is_read_as_a_hyphen() -> None:
    text = Q13.replace("-", "\u2011")

    assert check(text, ["23.3-8027-2021-004"]).problems == []
    assert check(text.replace("8952", "8951"), ["23.3-8027-2021-004"]).problems == [
        "Organisationsnumret 556549-8951 har fel kontrollsiffra. Kopiera det från search_register."
    ]


def test_another_declared_agreements_org_number_in_a_sentence_about_one_is_a_problem() -> None:
    # Gold q10's trap: Nordlo Improve AB's number, declared too, in the sentence about Advance.
    text = Q10.replace("556486-1689", "556271-9129")

    report = check(text, ["23.3-5890-2023-002", "23.3-10639-2023-007"])

    assert report.problems == [IMPROVES_ORG_NUMBER]
    assert report.unbacked == ["556271-9129"]


def test_an_undeclared_agreements_org_number_is_a_problem() -> None:
    report = check(Q10.replace("556486-1689", "556271-9129"), ["23.3-5890-2023-002"])

    assert report.problems == [IMPROVES_ORG_NUMBER]


def test_a_supplier_name_without_its_legal_form_names_the_agreement_of_its_sentence() -> None:
    text = "Nordlo Advance har organisationsnummer 556271-9129. Nordlo Improve AB har avtal -007."

    report = check(text, ["23.3-5890-2023-002", "23.3-10639-2023-007"])

    assert report.problems == [IMPROVES_ORG_NUMBER]


def test_a_sentence_that_names_both_agreements_may_take_either_agreements_values() -> None:
    text = "Nordlo Advance AB och Nordlo Improve AB har organisationsnummer 556271-9129."

    assert check(text, ["23.3-5890-2023-002", "23.3-10639-2023-007"]).problems == []


def test_a_former_name_names_the_agreement_of_its_sentence() -> None:
    text = "EPM Data har organisationsnummer 556271-9129. Nordlo Improve AB har avtal -007."

    report = check(text, ["23.3-5890-2023-002", "23.3-10639-2023-007"])

    assert report.problems == [IMPROVES_ORG_NUMBER]


def test_a_supplier_name_in_the_genitive_names_the_agreement_of_its_sentence() -> None:
    text = "Nordlo Advances organisationsnummer är 556271-9129. Nordlo Improve AB har avtal -007."

    report = check(text, ["23.3-5890-2023-002", "23.3-10639-2023-007"])

    assert report.problems == [IMPROVES_ORG_NUMBER]


NORDLO = ["23.3-5890-2023-002", "23.3-10639-2023-007"]


@pytest.mark.parametrize(
    ("text", "declared", "sections", "user_texts", "problems"),
    [
        (
            # Iver Sverige AB's number, which the register does not have, the section backs.
            "Iver Sverige AB (556575-3042) har lägst pris [1], före Nordlo Advance AB "
            "(23.3-5890-2023-002) med organisationsnummer 556271-9129. Nordlo Improve AB har "
            "avtal 23.3-10639-2023-007.",
            NORDLO,
            [section(PRICE_LIST + " | Nordlo Improve AB | 556271-9129 | 650")],
            [],
            [IMPROVES_ORG_NUMBER],
        ),
        (
            "Ja. Nordlo Advance AB (23.3-5890-2023-002) har organisationsnummer 556271-9129. "
            "Nordlo Improve AB har avtal 23.3-10639-2023-007.",
            NORDLO,
            [],
            ["Är 556271-9129 Nordlo Advance AB:s organisationsnummer?"],
            [IMPROVES_ORG_NUMBER],
        ),
        (
            "Telia Cygate AB:s avtal 23.3-8027-2021-004 gäller till 2030-03-09. Castra Group AB "
            "har -001.",
            ["23.3-8027-2021-004", "23.3-8321-2024-001"],
            [],
            ["Gäller Telia Cygates avtal till 2030-03-09?"],
            [
                "Datumet 2030-03-09 står inte i registret för 23.3-8027-2021-004 (giltigt "
                "2023-02-18–2027-02-17) och inte i något citerat avsnitt. Rätta det, eller räkna "
                "det med calculate_date och skriv dess step i meningen."
            ],
        ),
    ],
    ids=["a section's org number", "the user's org number", "the user's date"],
)
def test_a_section_or_the_user_does_not_back_another_declared_agreements_value(
    text: str,
    declared: list[str],
    sections: list[CitedSection],
    user_texts: list[str],
    problems: list[str],
) -> None:
    report = check(text, declared, sections=sections, user_texts=user_texts)

    assert report.problems == problems


@pytest.mark.parametrize(
    ("text", "problems"),
    [
        (
            "Avtal 23.3-5890-2023-002 har enligt bl.a. Registret organisationsnummer 556271-9129.",
            [IMPROVES_ORG_NUMBER],
        ),
        ("Avtal 23.3-5890-2023-002 finns. Registret ger organisationsnummer 556271-9129.", []),
    ],
    ids=["an abbreviation", "a full stop"],
)
def test_a_sentence_ends_at_a_full_stop_but_not_after_an_abbreviation(
    text: str, problems: list[str]
) -> None:
    assert check(text, ["23.3-5890-2023-002", "23.3-10639-2023-007"]).problems == problems


def test_a_sentence_does_not_end_inside_a_numbered_sub_area_name() -> None:
    # Chas Visual Management AB's number in the sentence about Castra Group AB.
    text = (
        "Castra Group AB har avtal 23.3-8321-2024-001 på IT-konsulttjänster 3. IT-säkerhet med "
        "organisationsnummer 556726-4758. Chas Visual Management AB har -002."
    )

    report = check(text, ["23.3-8321-2024-001", "23.3-8321-2024-002"])

    assert report.problems == [
        "Organisationsnumret 556726-4758 hör inte till 23.3-8321-2024-001 (registret: "
        "556958-4401). Kopiera rätt nummer från search_register."
    ]


def test_a_long_run_of_spaces_is_checked_quickly() -> None:
    text = "Avtal 23.3-8027-2021-004 gäller" + " " * 40_000 + "till 2027-02-17."

    started = time.perf_counter()
    report = check(text, ["23.3-8027-2021-004"])
    elapsed = time.perf_counter() - started

    assert report.problems == []
    assert elapsed < 0.5  # a few milliseconds; seconds when the sentence split is quadratic


def test_another_declared_agreements_date_in_a_sentence_about_one_is_a_problem() -> None:
    text = (
        "Telia Cygate AB:s avtal 23.3-8027-2021-004 gäller till 2030-03-09. Castra Group AB har "
        "-001."
    )

    report = check(text, ["23.3-8027-2021-004", "23.3-8321-2024-001"])

    assert report.problems == [
        "Datumet 2030-03-09 står inte i registret för 23.3-8027-2021-004 (giltigt "
        "2023-02-18–2027-02-17) och inte i något citerat avsnitt. Rätta det, eller räkna det med "
        "calculate_date och skriv dess step i meningen."
    ]


def test_a_sentence_that_names_no_agreement_may_take_any_declared_agreements_values() -> None:
    text = (
        "Telia Cygate AB och Castra Group AB har avtal. De gäller till 2027-02-17 och 2030-03-09."
    )

    assert check(text, ["23.3-8027-2021-004", "23.3-8321-2024-001"]).problems == []


def test_a_date_that_is_no_day_of_the_calendar_is_a_problem() -> None:
    report = check("Avtal 23.3-8027-2021-004 gäller till 2027-02-30.", ["23.3-8027-2021-004"])

    assert report.problems == ["Datumet 2027-02-30 finns inte i kalendern. Rätta det."]
    assert report.unbacked == ["2027-02-30"]


def test_each_problem_and_value_is_listed_once_in_the_order_of_the_text() -> None:
    text = (
        "Avtal 23.3-8027-2021-004 gäller till 2027-02-18. Telia Cygate AB, 556549-8951, har "
        "avtalet till 2027-02-18."
    )

    report = check(text, ["23.3-8027-2021-004"])

    assert [problem.split()[1] for problem in report.problems] == ["2027-02-18", "556549-8951"]
    assert report.unbacked == ["2027-02-18", "556549-8951"]


# --- the declared agreements ---------------------------------------------------------


def test_a_declared_agreement_the_text_does_not_use_is_a_problem() -> None:
    report = check(Q10, ["23.3-5890-2023-002", "23.3-8027-2021-004"])

    assert report.problems == [
        "Avtalet 23.3-8027-2021-004 i register_facts används inte i texten. Ta bort det ur "
        "register_facts om texten inte tar några uppgifter om det ur registret."
    ]
    assert report.unbacked == []  # nothing in the text is unbacked
    assert report.facts == [fact(ADVANCE), fact(TELIA)]


@pytest.mark.parametrize(
    "text",
    [
        "Telia Cygate AB har ett avtal i Programvarulösningar.",
        "Telia Cygate har ett avtal i Programvarulösningar.",
        "Organisationsnummer 556549-8952 har ett avtal i Programvarulösningar.",
        "Avtalet 23.3.8027-21:004 gäller Programvarulösningar.",
        "Telia Cygates avtal gäller till 2027-02-17.",
    ],
    ids=[
        "supplier name",
        "without the legal form",
        "org number",
        "a document's spelling",
        "in the genitive",
    ],
)
def test_a_declared_agreement_is_used_by_its_number_org_number_or_supplier_name(
    text: str,
) -> None:
    assert check(text, ["23.3-8027-2021-004"]).problems == []


def test_an_undeclared_agreement_number_is_a_problem() -> None:
    report = check(Q10.replace("2023-002", "2023-003"), ["23.3-5890-2023-002"])

    assert report.problems == [
        "Avtalsnumret 23.3-5890-2023-003 i texten finns inte i register_facts eller i något "
        "citerat avsnitt. Lägg det i register_facts om uppgiften kommer från search_register, "
        "annars rätta det."
    ]
    assert report.unbacked == ["23.3-5890-2023-003"]


@pytest.mark.parametrize("sequence", ["0021", "1002", "2"])
def test_an_agreement_number_with_a_digit_too_many_or_too_few_is_no_agreement_number(
    sequence: str,
) -> None:
    # Not read as the procurement 23.3-5890-2023, which the declared agreement backs.
    number = f"23.3-5890-2023-{sequence}"

    report = check(Q10.replace("23.3-5890-2023-002", number), ["23.3-5890-2023-002"])

    assert report.problems == [
        f"Avtalsnumret {number} i texten är inget avtalsnummer. Kopiera numret från "
        "search_register, eller rätta det."
    ]
    assert report.unbacked == [number]


@pytest.mark.parametrize(
    "agreements",
    [
        "23.3-8321-2024-001 till -008",
        "23.3-8321-2024-001 till –008",
        "23.3-8321-2024-001 t.o.m. -008",
        "23.3-8321-2024-001–008",
    ],
    ids=["till", "an en dash", "t.o.m.", "no space"],
)
def test_a_range_of_agreements_uses_each_agreement_in_it(agreements: str) -> None:
    text = (
        f"Åtta leverantörer har avtal på IT-säkerhet (avtal {agreements}). Alla avtal gäller "
        "2026-03-10 – 2030-03-09."
    )

    assert check(text, numbers(IT_SECURITY)).problems == []


def test_a_short_sequence_is_read_by_the_case_number_before_it_in_its_sentence() -> None:
    text = (
        "Sju avtal på Övre Norrland: 23.3-14537-2023-002, (-005), -006, -012, -013, -014 och "
        "–015. De gäller 2025-04-03–2029-04-02."
    )

    assert check(text, numbers(HELPDESK)).problems == []


def test_a_short_sequence_the_register_facts_lack_is_a_problem() -> None:
    report = check(Q11.replace("AB (-005)", "AB (-007)"), numbers(HELPDESK))

    assert report.problems == [
        "Avtalsnumret 23.3-14537-2023-007 i texten finns inte i register_facts eller i något "
        "citerat avsnitt. Lägg det i register_facts om uppgiften kommer från search_register, "
        "annars rätta det."
    ]
    assert report.unbacked == ["23.3-14537-2023-007"]


def test_register_values_with_nothing_declared_are_each_a_problem() -> None:
    report = check(Q10, [])

    assert report.unbacked == ["23.3-5890-2023-002", "556486-1689", "2024-11-14", "2028-11-13"]
    assert report.problems[1] == (
        "Organisationsnumret 556486-1689 står inte i registret för något avtal i register_facts "
        "och inte i något citerat avsnitt. Kopiera det från search_register och lägg "
        "avtalsnumret i register_facts, eller ta bort det."
    )
    assert len(report.problems) == 4
    assert report.facts == []


def test_a_declared_number_the_register_does_not_have_is_a_problem_of_its_own() -> None:
    text = Q10 + " Även avtal 23.3-5890-2023-099 finns."

    report = check(text, ["23.3-5890-2023-002", "23.3-5890-2023-099"])

    # The text's mention is not a second problem: the declaration's says what is wrong.
    assert report.problems == [
        "Avtalsnumret 23.3-5890-2023-099 i register_facts finns inte i registret. Kopiera numret "
        "från search_register, eller ta bort det ur register_facts."
    ]
    assert report.unbacked == ["23.3-5890-2023-099"]
    assert report.facts == [fact(ADVANCE)]


def test_a_declared_value_that_is_no_agreement_number_is_a_problem() -> None:
    report = check(Q10, ["23.3-5890-2023-002", "Nordlo Advance AB"])

    assert report.problems == [
        "Avtalsnumret Nordlo Advance AB i register_facts är inget avtalsnummer. Kopiera numret "
        "från search_register, eller ta bort det ur register_facts."
    ]
    assert report.unbacked == ["Nordlo Advance AB"]


def test_both_spellings_of_one_agreement_number_are_one_agreement() -> None:
    text = (
        "Digital Interpretations Scandinavia AB har avtal 23.3-12000-2020-01 (även skrivet "
        "23.3-12000-2020-001), giltigt 2023-02-15–2027-02-14."
    )

    report = check(text, ["23.3-12000-2020-001", "23.3-12000-2020-01"])

    assert report.problems == []
    assert report.facts == [fact(entry) for entry in DIGITAL_INTERPRETATIONS]  # once each


def test_the_procurement_number_of_a_declared_agreement_is_backed() -> None:
    text = "Upphandlingen 23.3-5890-2023 gav Nordlo Advance AB avtal 23.3-5890-2023-002."

    assert check(text, ["23.3-5890-2023-002"]).problems == []
    assert check(
        text.replace("23.3-5890-2023 ", "23.3-5891-2023 "), ["23.3-5890-2023-002"]
    ).problems == [
        "Numret 23.3-5891-2023 i texten hör inte till något avtal i register_facts och står inte "
        "i något citerat avsnitt. Lägg avtalsnumret i register_facts om uppgiften kommer från "
        "search_register, annars rätta det."
    ]


def test_an_extension_date_of_the_register_backs_the_text() -> None:
    extended = TELIA.model_copy(update={"max_extension_to": date(2028, 2, 17)})
    draft = FinalAnswer(
        answered=True,
        text="Avtal 23.3-8027-2021-004 kan förlängas till 2028-02-17.",
        register_facts=["23.3-8027-2021-004"],
    )

    report = check_register_facts(draft, {"23.3-8027-2021-004": [extended]}, [], [], TODAY)

    assert report.problems == []
    assert report.facts[0].max_extension_to == date(2028, 2, 17)


# --- what else backs a value ---------------------------------------------------------


def test_a_date_in_a_passed_sections_text_backs_the_text() -> None:
    assert check(Q24, [], sections=[section(PRICE_LIST)]).problems == []


def test_a_date_without_a_passed_section_is_a_problem() -> None:
    report = check(Q24, [])

    assert report.problems == [
        "Datumet 2026-09-01 står inte i registret för något avtal i register_facts och inte i "
        "något citerat avsnitt. Rätta det, eller räkna det med calculate_date och skriv dess step "
        "i meningen."
    ]


def test_a_date_in_a_passed_sections_heading_backs_the_text_in_either_form() -> None:
    heading = (
        "Ny version av Microsofts definition av kvalificerad utbildningsanvändare från 1 februari "
        "2023 exkluderar offentliga bibliotek och museer"
    )
    passed = [section("Text utan datum.", title=heading)]

    assert (
        check("Sedan 1 februari 2023 omfattas inte bibliotek.", [], sections=passed).problems == []
    )
    assert check("Sedan 2023-02-01 omfattas inte bibliotek.", [], sections=passed).problems == []


def test_a_date_the_user_wrote_is_backed() -> None:
    text = "Ja. Telia Cygate AB:s avtal 23.3-8027-2021-004 gäller 2027-01-01 och till 2027-02-17."

    report = check(
        text, ["23.3-8027-2021-004"], user_texts=["Gäller Telia Cygates avtal 1 januari 2027?"]
    )

    assert report.problems == []


def test_today_is_backed() -> None:
    text = "Idag, 2026-10-07, gäller Telia Cygate AB:s avtal 23.3-8027-2021-004."

    assert check(text, ["23.3-8027-2021-004"]).problems == []


def test_long_form_dates_are_compared_with_the_register() -> None:
    text = "Avtal 23.3-8027-2021-004 gäller från 18 februari 2023 till 17 februari 2027."

    assert check(text, ["23.3-8027-2021-004"]).problems == []
    assert check(text.replace("17 februari", "17 mars"), ["23.3-8027-2021-004"]).problems == [
        "Datumet 17 mars 2027 står inte i registret för 23.3-8027-2021-004 (giltigt "
        "2023-02-18–2027-02-17) och inte i något citerat avsnitt. Rätta det, eller räkna det med "
        "calculate_date och skriv dess step i meningen."
    ]


# --- computed dates ------------------------------------------------------------------

EXPIRY = "Telia Cygate AB:s avtal 23.3-8027-2021-004 gäller till 2027-02-17. "


@pytest.mark.parametrize(
    "computation",
    [
        "Med tre (3) månaders uppsägningstid ska uppsägningen ske senast 2026-11-17.",
        "Säg upp avtalet senast 3 månader före, alltså 2026-11-17.",
        "En förlängning med 24 månader från 2027-02-17 ger ett avtal till 2029-02-16.",
        "Om 30 dagar, 2026-11-06, gäller avtalet fortfarande.",
        "Avtalet kan förlängas med tjugofyra (24) månader, till 2029-02-17.",
        "En reklamation idag ska besvaras inom fjorton (14) dagar, alltså senast 2026-10-21.",
        "Avtalet började 2023-02-18; trettiosex månader senare, 2026-02-18, kunde det sägas upp.",
        "Avbeställ inom tio (10) Arbetsdagar före slutet, alltså senast 2027-02-03.",
        "Med 14 kalenderdagars uppsägningstid från i dag upphör det 2026-10-21.",
        "Konsulten ska börja inom 15 arbetsdagar från i dag, senast 2026-10-29.",
    ],
    ids=[
        "months before",
        "digits",
        "an inclusive end",
        "from today",
        "tjugofyra (24)",
        "fjorton",
        "trettiosex",
        "working days",
        "calendar days",
        "a working day off",
    ],
)
def test_a_computed_date_with_its_calculation_is_backed(computation: str) -> None:
    assert check(EXPIRY + computation, ["23.3-8027-2021-004"]).problems == []


@pytest.mark.parametrize(
    "computation",
    [
        "Uppsägningen ska ske senast 2026-11-17.",
        "Med tre månaders uppsägningstid ska uppsägningen ske senast 2026-10-17.",
    ],
    ids=["no calculation", "a wrong calculation"],
)
def test_a_computed_date_without_a_right_calculation_is_a_problem(computation: str) -> None:
    report = check(EXPIRY + computation, ["23.3-8027-2021-004"])

    assert len(report.problems) == 1
    assert report.problems[0].startswith("Datumet 2026-1")


@pytest.mark.parametrize(
    ("text", "declared", "problems"),
    [
        (
            "Avtal 23.3-5890-2023-002, organisationsnummer 556486-1689. Avtalet gäller i fyra år, "
            "2024-11-14–2028-11-14.",
            ["23.3-5890-2023-002"],
            [WRONG_DATE],
        ),
        (
            "Telia Cygate AB:s avtal 23.3-8027-2021-004 gäller från 2023-02-18 i fyra år, till "
            "2027-02-18.",
            ["23.3-8027-2021-004"],
            [
                "Datumet 2027-02-18 står inte i registret för 23.3-8027-2021-004 (giltigt "
                "2023-02-18–2027-02-17) och inte i något citerat avsnitt. Rätta det, eller räkna "
                "det med calculate_date och skriv dess step i meningen."
            ],
        ),
    ],
    ids=["a sentence that names none", "a sentence that names it"],
)
def test_a_date_a_day_off_the_registers_is_a_near_miss_not_a_computed_date(
    text: str, declared: list[str], problems: list[str]
) -> None:
    assert check(text, declared).problems == problems


# --- calculations written out (calculate_date's step) -------------------------------

TELIA_NUMBER = "23.3-8027-2021-004"


@pytest.mark.parametrize(
    "computation",
    [
        "Säg upp avtalet senast 2027-02-17 minus 3 månader = 2026-11-17.",
        "Med symboler: 2027-02-17 - 3 månader = 2026-11-17.",
        # A day off the register's date, which alone would be a near miss.
        "Sista dagen före slutet är 2027-02-17 minus 1 dag = 2027-02-16.",
        "Konsulten ska börja senast 2026-10-07 plus 15 arbetsdagar = 2026-10-28.",
        "Avbeställ senast 2027-02-17 minus 10 arbetsdagar = 2027-02-03.",
        "Om 90 arbetsdagar: 2026-10-07 plus 90 arbetsdagar = 2027-02-15.",
        "Förlängt: 2027-02-17 plus 24 månader = 2029-02-17, minus 1 dag = 2029-02-16.",
        "Varsla 2027-02-17 minus 3 månader = 2026-11-17, minus 2 veckor = 2026-11-03.",
        "Säg upp 2027-02-17 minus 3 månader = 2026-11-17. Påminn 2026-11-17 minus 2 veckor = "
        "2026-11-03.",
        "Viktiga datum: 2027-02-17 minus 3 månader = 2026-11-17, minus 6 månader = 2026-08-17.",
        "Med tankstreck: 2027-02-17 – 3 månader = 2026-11-17.",
        "Med aftnarna som helgdagar: 2027-02-17 minus 40 arbetsdagar = 2026-12-16.",
        "Avbeställ senast 2027-02-17 minus 40 arbetsdagar = 2026-12-18. Räknas julafton och "
        "nyårsafton som helgdagar blir det 2026-12-16.",
        "Säg upp tre månader före slutet, 2026-11-17. Påminn 2026-11-17 minus 2 veckor = "
        "2026-11-03.",
    ],
    ids=[
        "months",
        "a minus sign",
        "a day off the register",
        "working days after",
        "working days before",
        "over the holidays",
        "a period's last day",
        "a chain",
        "a chain in two sentences",
        "a list from one date",
        "an en dash",
        "the eves as holidays",
        "the note's other date",
        "from a computed date",
    ],
)
def test_a_calculation_written_out_and_right_backs_its_date(computation: str) -> None:
    assert check(EXPIRY + computation, [TELIA_NUMBER]).problems == []


def wrong(calculation: str, right: str) -> str:
    return (
        f"Uträkningen {calculation} stämmer inte: det blir {right}. Räkna med calculate_date och "
        "skriv dess step i meningen."
    )


@pytest.mark.parametrize(
    ("computation", "problem"),
    [
        # A day off: the offset in the sentence alone would let it pass.
        (
            "Säg upp senast 2027-02-17 minus 3 månader = 2026-11-18.",
            wrong("2027-02-17 minus 3 månader = 2026-11-18", "2026-11-17"),
        ),
        (
            "Börja senast 2026-10-07 plus 15 arbetsdagar = 2026-10-22.",
            wrong("2026-10-07 plus 15 arbetsdagar = 2026-10-22", "2026-10-28"),
        ),
        # Both dates are the register's, but the calculation is not right.
        (
            "Avtalet gäller 2023-02-18 plus 48 månader = 2027-02-17.",
            wrong("2023-02-18 plus 48 månader = 2027-02-17", "2027-02-18"),
        ),
        (
            "Varsla 2027-02-17 minus 3 månader = 2026-11-17, minus 2 veckor = 2026-11-04.",
            wrong(
                "2026-11-17 minus 2 veckor = 2026-11-04",
                "2026-11-03, och 2027-02-03 från 2027-02-17",
            ),
        ),
        (
            "Säg upp senast 2027-02-17 – 3 månader = 2026-11-18.",
            wrong("2027-02-17 - 3 månader = 2026-11-18", "2026-11-17"),
        ),
    ],
    ids=[
        "a day off",
        "working days",
        "dates the register has",
        "the second of a chain",
        "an en dash",
    ],
)
def test_a_wrong_calculation_is_a_problem_that_gives_the_right_date(
    computation: str, problem: str
) -> None:
    assert check(EXPIRY + computation, [TELIA_NUMBER]).problems == [problem]


@pytest.mark.parametrize(
    ("computation", "claimed"),
    [
        (
            "Sista dag att säga upp är 2026-11-16 (2027-02-17 minus 3 månader = 2026-11-17).",
            "2026-11-16",
        ),
        (
            "Sista dag att säga upp är 2026-11-18 (2027-02-17 minus 3 månader = 2026-11-17).",
            "2026-11-18",
        ),
        (
            "Avbeställ senast 2027-02-04 (2027-02-17 minus 10 arbetsdagar = 2027-02-03).",
            "2027-02-04",
        ),
    ],
    ids=["a day before", "a day after", "a working day after"],
)
def test_a_date_next_to_a_calculation_must_be_its_result(computation: str, claimed: str) -> None:
    report = check(EXPIRY + computation, [TELIA_NUMBER])

    assert [problem.split(" står ")[0] for problem in report.problems] == [f"Datumet {claimed}"]


def test_a_calculation_from_a_date_nothing_backs_backs_nothing() -> None:
    report = check(EXPIRY + "Från 2025-01-01 plus 3 månader = 2025-04-01.", [TELIA_NUMBER])

    assert [problem.split(" står ")[0] for problem in report.problems] == [
        "Datumet 2025-01-01",
        "Datumet 2025-04-01",
    ]


# --- no false hits -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Se avsnitt 6.21.9 i de allmänna villkoren.",
        "Villkoren står i avsnitt 23.1-23.12 och 22.1-22.15.",
        "Uppsägning regleras i avsnitt 23.1.10–23.1.12 och 23.4.11-13.",
        "Avsnitt 23.3.12.20 gäller vite.",
        "Timpriset är 1 500 kr och vitet 2,5 % per påbörjad vecka, högst 1 250 000 kr.",
        "Ring 08-700 08 00, 070-123 45 67, 0771-123 456 eller +46 8 700 08 00.",
        "Avtalet upphandlades 2023 och priserna gäller 2023–2024.",
        "Version 2.1 från mars 2025 ersätter version 1.4.",
    ],
    ids=[
        "section number",
        "ranges",
        "chapter 23 ranges",
        "dotted section",
        "prices",
        "phone numbers",
        "years",
        "month",
    ],
)
def test_section_numbers_ranges_prices_phone_numbers_and_years_are_no_register_values(
    text: str,
) -> None:
    assert check(text, []) == RegisterReport(problems=[], facts=[], unbacked=[])


@pytest.mark.parametrize(
    "numbers_after",
    [
        "kostar 1 500–2 000 kr, gäller 2023–2024, s. 100-120, och priset sjönk -10 %",
        "ring 08-700 08 00 eller 0771-123 456",
        "har värdena -0050, -12,5 och -100-200",
    ],
    ids=["prices and years", "phone numbers", "other shapes"],
)
def test_ordinary_numbers_after_a_case_number_are_no_short_sequences(numbers_after: str) -> None:
    text = f"Telia Cygate AB:s avtal 23.3-8027-2021-004 {numbers_after}."

    report = check(text, ["23.3-8027-2021-004"])

    assert report.problems == []


def test_a_short_sequence_without_a_case_number_before_it_in_its_sentence_is_not_read() -> None:
    text = "Telia Cygate AB har avtal 23.3-8027-2021-004. Priset sjönk med -150 kr."

    assert check(text, ["23.3-8027-2021-004"]).problems == []
