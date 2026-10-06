"""Tests for avtalsagent.ingestion.checks.supplier_party.

The party clauses and register rows are real, from the pilot (M4 survey,
suppliers.md §3, §6), cited as sha[:12] and PDF page: 185872a6bb90 p3 (Crayon AB,
card 23.3-2649-2022-003), 7a49e1a61b31 p7 (ÅF Digital Solutions AB, card
23.3-2940-20:033), b0f5951c99b2 p7 (Knowit & Precio Fishbone Public IT AB, card
23.3-2940-20:012), 77d641b81cc2 p3 (Chas visual management AB, card
23.3-2649-2022-002), 171a3cacf5fd p1 (Microsoft, not a card). The register's former
name "ÅF-Infrastructure AB" is real (23.3-4104-2022-003); a card naming it, a card
for an agreement the register lacks and "Exempelkommunen" are made up. The names in
the normalisation test are from the supplier tables of 8d679cb2ebef and aebc63b78a54.
"""

from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Fact,
    FactKind,
    Finding,
    Severity,
)
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.supplier_party import (
    normalise_name,
    run,
    supplier_parties,
)

SHA = "12" * 32
PREFIX = (
    "mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829 nedan "
    "Kammarkollegiet, och "
)
CRAYON = PREFIX + "Crayon AB, organisationsnummer 556635-9799, nedan Ramavtalsleverantören"
AF = PREFIX + "ÅF Digital Solutions AB, organisationsnummer 556866-4444 nedan Ramavtalsleverantören"
KNOWIT = (
    PREFIX + "Knowit & Precio Fishbone Public IT AB, organisationsnummer 559309-6794, nedan "
    "Ramavtalsleverantören"
)


def entry(
    agreement: str, org_number: str, supplier: str, former: str | None = None
) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=agreement,
        procurement_number=agreement[:-4],
        org_number=org_number,
        supplier_name=supplier,
        former_supplier_name=former,
        framework_area="IT-konsulttjänster Resurskonsulter",
        sub_area_path=("IT-konsulttjänster Resurskonsulter",),
        valid_from=date(2022, 12, 1),
        valid_to=date(2026, 11, 30),
        max_extension_to=None,
    )


REGISTER = (
    entry("23.3-2649-2022-003", "556635-9799", "Crayon AB"),
    entry("23.3-2649-2022-002", "556726-4758", "Chas Visual Management AB"),
    entry("23.3-2940-20:033", "556224-8012", "AFRY Sweden AB"),
    entry("23.3-2940-20:012", "559309-6794", "Knowit Public IT AB"),
    entry("23.3-4104-2022-003", "556185-2103", "AFRY Infrastructure AB", "ÅF-Infrastructure AB"),
)


def party(org_number: str, name: str, clause: str, rule: str = "E1") -> Fact:
    return Fact(
        kind=FactKind.PARTY, value=org_number, raw=clause, rule=rule, block=38, page=7, name=name
    )


def checked(facts: list[Fact], card: str | None) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=SHA,
        title="Ramavtal",
        document_type=DocumentType.SUPPLIER_AGREEMENT,
        type_rule="R01",
        agreement_number=card,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=None,
        is_template=False,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    link = CatalogLink(
        sha256=SHA,
        url="https://www.avropa.se/globalassets/ramavtal.pdf",
        title="Ramavtal",
        category=None,
        agreement_number=card,
        site_updated=None,
        page_url="https://www.avropa.se/ramavtal/sidan",
        page_title="Sidan",
        page_procurement_numbers=("23.3-2940-20",),
        page_period=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=tuple(facts), mentions=())
    return CheckedFile(extraction, (link,), (), "pdf", 7, ())


def check(file: CheckedFile) -> list[Finding]:
    return run(CheckContext(files=(file,), links=file.links, register=REGISTER, areas=()))


def test_the_registers_supplier_has_no_finding() -> None:
    crayon = party("556635-9799", "Crayon AB", CRAYON)

    assert check(checked([crayon], "23.3-2649-2022-003")) == []


def test_another_organisation_number_quarantines_the_card() -> None:
    af = party("556866-4444", "ÅF Digital Solutions AB", AF)

    assert check(checked([af], "23.3-2940-20:033")) == [
        Finding(
            check="supplier_party",
            severity=Severity.QUARANTINE,
            subject="556866-4444",
            message=(
                "Leverantörskortet för avtal 23.3-2940-20:033 har ÅF Digital Solutions AB, "
                "organisationsnummer 556866-4444, som avtalspart, men registret har AFRY Sweden "
                "AB, 556224-8012, för avtalet. Organisationsnumret 556866-4444 finns inte i "
                "registret."
            ),
            sha256=SHA,
            agreement_number="23.3-2940-20:033",
            evidence=AF,
        )
    ]


def test_a_number_the_register_has_for_another_supplier_is_named() -> None:
    crayon = party("556635-9799", "Crayon AB", CRAYON)

    findings = check(checked([crayon], "23.3-2940-20:033"))

    assert [(f.severity, f.subject) for f in findings] == [(Severity.QUARANTINE, "556635-9799")]
    assert findings[0].message.endswith("I registret är 556635-9799 Crayon AB.")


def test_a_card_for_an_agreement_the_register_lacks_is_quarantined() -> None:
    crayon = party("556635-9799", "Crayon AB", CRAYON)

    findings = check(checked([crayon], "23.3-2649-2022-009"))

    assert [(f.severity, f.subject, f.agreement_number) for f in findings] == [
        (Severity.QUARANTINE, "556635-9799", "23.3-2649-2022-009")
    ]
    assert findings[0].message.endswith("men avtalet finns inte i registret.")


def test_the_same_number_under_another_name_is_a_note() -> None:
    knowit = party("559309-6794", "Knowit & Precio Fishbone Public IT AB", KNOWIT)

    assert check(checked([knowit], "23.3-2940-20:012")) == [
        Finding(
            check="supplier_party",
            severity=Severity.NOTE,
            subject="Knowit & Precio Fishbone Public IT AB",
            message=(
                "Leverantörskortet för avtal 23.3-2940-20:012 skriver avtalsparten Knowit & "
                "Precio Fishbone Public IT AB, men registret har namnet Knowit Public IT AB för "
                "samma organisationsnummer (559309-6794)."
            ),
            sha256=SHA,
            agreement_number="23.3-2940-20:012",
            evidence=KNOWIT,
        )
    ]


def test_a_name_differing_only_in_case_is_the_registers() -> None:
    chas = party("556726-4758", "Chas visual management AB", "och Chas visual management AB")

    assert check(checked([chas], "23.3-2649-2022-002")) == []


def test_the_registers_former_name_is_the_registers() -> None:
    former = party("556185-2103", "ÅF-Infrastructure AB", "och ÅF-Infrastructure AB")

    assert check(checked([former], "23.3-4104-2022-003")) == []


def test_normalise_name_drops_case_punctuation_and_the_swedish_company_form() -> None:
    assert normalise_name("Knowit aktiebolag (publ)") == normalise_name("Knowit Aktiebolag")
    assert normalise_name("Combitech AB") == normalise_name("Combitech Aktiebolag")
    assert normalise_name("Microsoft Ireland Operations Ltd") == "microsoft ireland operations ltd"
    assert normalise_name("Tieto Sweden AB") != normalise_name("Tieto AB")


def test_only_the_supplier_slot_of_a_card_is_compared() -> None:
    # A file outside the cards (171a3cacf5fd) and a card's customer slot.
    microsoft = party("502052-1307", "Microsoft Ireland Operations Ltd", "och Microsoft")
    customer = party("212000-9999", "Exempelkommunen", "mellan Exempelkommunen", "E1-customer")

    assert supplier_parties(checked([microsoft], None)) == []
    assert check(checked([microsoft], None)) == []
    assert check(checked([customer], "23.3-2649-2022-003")) == []
