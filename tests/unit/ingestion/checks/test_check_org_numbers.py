"""Tests for avtalsagent.ingestion.checks.org_numbers.

The numbers, clauses and register rows are real, from the pilot, cited as
sha[:12] and PDF page: 8d679cb2ebef p1, the supplier table of "Prisbilaga -
sammanställning Delområde 1" (page 23.3-1688-2024) with "Castra Group AB"
556958-4401 and "ÅF Digital Solutions AB" 556866-4444; 171a3cacf5fd p1, the
Microsoft party clause; 7a49e1a61b31 p7, the ÅF party clause of card
23.3-2940-20:033; Kammarkollegiet's 202100-0829 in every footer. A table with
AFRY's number on the IT-säkerhet page, the mistyped number, the Tieto numbers in
the ÅF card and "Exempelkommunen" with its number are made up.
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
from avtalsagent.ingestion.checks.org_numbers import run

SHA = "ef" * 32
MICROSOFT_CLAUSE = (
    "mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829 "
    "(nedan Kammarkollegiet) och Microsoft Ireland Operations Ltd, organisationsnummer "
    "502052-1307 (nedan Microsoft"
)
AF_CLAUSE = (
    "mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829 nedan "
    "Kammarkollegiet, och ÅF Digital Solutions AB, organisationsnummer 556866-4444 nedan "
    "Ramavtalsleverantören"
)


def entry(agreement: str, procurement: str, org_number: str, supplier: str) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=agreement,
        procurement_number=procurement,
        org_number=org_number,
        supplier_name=supplier,
        former_supplier_name=None,
        framework_area="IT-konsulttjänster Resurskonsulter",
        sub_area_path=("IT-konsulttjänster Resurskonsulter",),
        valid_from=date(2022, 12, 1),
        valid_to=date(2026, 11, 30),
        max_extension_to=None,
    )


REGISTER = (
    entry("23.3-1688-2024-001", "23.3-1688-2024", "556958-4401", "Castra Group AB"),
    entry("23.3-1688-2024-009", "23.3-1688-2024", "556224-8012", "AFRY Sweden AB"),
    entry("23.3-8321-2024-001", "23.3-8321-2024", "556958-4401", "Castra Group AB"),
    entry("23.3-2940-20:033", "23.3-2940-20", "556224-8012", "AFRY Sweden AB"),
    entry("23.3-2940-20:017", "23.3-2940-20", "559435-9001", "Tieto AB"),
    entry("23.5-3718-2024", "23.5-3718-2024", "556233-4804", "Microsoft AB"),
)


def org(value: str, page: int = 1) -> Fact:
    return Fact(kind=FactKind.ORG_NUMBER, value=value, raw=value, rule="ORG", block=0, page=page)


def party(value: str, name: str, clause: str, rule: str = "E1") -> Fact:
    return Fact(kind=FactKind.PARTY, value=value, raw=clause, rule=rule, block=0, page=1, name=name)


def checked(facts: list[Fact], procurement: str, card: str | None = None) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=SHA,
        title="Ramavtal" if card else "Prisbilaga",
        document_type=DocumentType.SUPPLIER_AGREEMENT if card else DocumentType.PRICE_ANNEX,
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
        url="https://www.avropa.se/globalassets/prisbilaga.pdf",
        title=metadata.title,
        category=None if card else "Avtal",
        agreement_number=card,
        site_updated=None,
        page_url="https://www.avropa.se/ramavtal/sidan",
        page_title="Sidan",
        page_procurement_numbers=(procurement,),
        page_period=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=tuple(facts), mentions=())
    return CheckedFile(extraction, (link,), (), "pdf", 1, ())


def check(file: CheckedFile) -> list[Finding]:
    return run(CheckContext(files=(file,), links=file.links, register=REGISTER, areas=()))


def test_a_supplier_of_the_pages_procurement_has_no_finding() -> None:
    assert check(checked([org("556958-4401")], "23.3-1688-2024")) == []


def test_a_number_in_no_register_row_quarantines_the_file() -> None:
    findings = check(checked([org("556958-4401"), org("556866-4444")], "23.3-1688-2024"))

    assert findings == [
        Finding(
            check="org_numbers",
            severity=Severity.QUARANTINE,
            subject="556866-4444",
            message=(
                "Organisationsnumret 556866-4444 (s. 1) tillhör ingen leverantör på upphandlingen "
                "på sidan som länkar till dokumentet (23.3-1688-2024). Numret finns inte i "
                "registret."
            ),
            sha256=SHA,
            evidence="556866-4444",
        )
    ]


def test_a_supplier_of_another_procurement_only_quarantines_the_file() -> None:
    findings = check(checked([org("556224-8012")], "23.3-8321-2024"))

    assert [(f.severity, f.subject) for f in findings] == [(Severity.QUARANTINE, "556224-8012")]
    assert findings[0].message.endswith(
        "I registret är det AFRY Sweden AB, leverantör på 23.3-1688-2024, 23.3-2940-20."
    )


def test_kammarkollegiets_own_number_is_left_out() -> None:
    footers = [org("202100-0829", page=page) for page in (1, 2)]

    assert check(checked(footers, "23.3-1688-2024")) == []


def test_a_cards_supplier_slot_is_left_to_supplier_party() -> None:
    facts = [
        org("202100-0829"),
        org("556866-4444"),
        party("556866-4444", "ÅF Digital Solutions AB", AF_CLAUSE),
    ]

    assert check(checked(facts, "23.3-2940-20", card="23.3-2940-20:033")) == []


def test_a_party_outside_a_card_is_checked_with_its_clause_as_evidence() -> None:
    facts = [
        org("202100-0829"),
        org("502052-1307"),
        party("502052-1307", "Microsoft Ireland Operations Ltd", MICROSOFT_CLAUSE),
    ]

    findings = check(checked(facts, "23.5-3718-2024"))

    assert [(f.subject, f.evidence) for f in findings] == [("502052-1307", MICROSOFT_CLAUSE)]
    assert findings[0].message.startswith(
        "Organisationsnumret 502052-1307 (s. 1), som dokumentet anger för Microsoft Ireland "
        "Operations Ltd, tillhör ingen leverantör"
    )


def test_a_party_number_with_a_wrong_check_digit_is_checked() -> None:
    # Step 4 gives no ORG_NUMBER fact for it, only the PARTY fact.
    mistyped = party(
        "556233-4805", "Microsoft AB", "och Microsoft AB, organisationsnummer 556233-4805"
    )

    findings = check(checked([mistyped], "23.5-3718-2024"))

    assert [(f.severity, f.subject) for f in findings] == [(Severity.QUARANTINE, "556233-4805")]


def test_other_numbers_in_a_card_are_checked() -> None:
    facts = [
        org("556866-4444"),
        party("556866-4444", "ÅF Digital Solutions AB", AF_CLAUSE),
        org("559435-9001"),
        org("556052-7466"),
    ]

    findings = check(checked(facts, "23.3-2940-20", card="23.3-2940-20:033"))

    assert [f.subject for f in findings] == ["556052-7466"]


def test_a_customer_slot_is_not_left_out() -> None:
    customer = party("212000-9999", "Exempelkommunen", "mellan Exempelkommunen", "E1-customer")

    findings = check(checked([customer], "23.3-2940-20", card="23.3-2940-20:033"))

    assert [f.subject for f in findings] == ["212000-9999"]
