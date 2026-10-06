"""Tests for avtalsagent.ingestion.checks.agreement_number.

The lines and register rows are real, from the pilot (M4 survey, identifiers.md
§2), cited as sha[:12] and PDF page: 7a49e1a61b31 p1 "IT-konsulttjänster 2020 Dnr
23.3-2940-20 Ramavtal 23.3.2940-20:033 ÅF Digital Solutions AB" (the card of
23.3-2940-20:033); 65d611d12eab p2 [page_header] "23.3-8321-2024-001
IT-konsulttjänster - IT-säkerhet" and p3 "Ramavtal med avtalsnummer
23.3-8321-2024-XXX, har träffats för Avropsberättigades räkning" (the generic main
document of IT-säkerhet). The card linked from another supplier's card and the
cited agreement number are made up.
"""

from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Fact,
    FactKind,
    FactRole,
    Finding,
    Severity,
)
from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.agreement_number import run
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.extract.identifiers import find_identifiers

SHA = "cd" * 32
COVER = "IT-konsulttjänster 2020 Dnr 23.3-2940-20 Ramavtal 23.3.2940-20:033 ÅF Digital Solutions AB"
GENERIC_HEADER = "23.3-8321-2024-001 IT-konsulttjänster - IT-säkerhet"
GENERIC_CLAUSE = (
    "Ramavtal med avtalsnummer 23.3-8321-2024-XXX, har träffats för Avropsberättigades "
    "räkning, mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer "
    "202100-0829, nedan Kammarkollegiet, och Leverantörens namn, organisationsnummer "
    "Leverantörens organisationsnummer, nedan Ramavtalsleverantören."
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
    entry("23.3-2940-20:033", "23.3-2940-20", "556224-8012", "AFRY Sweden AB"),
    entry("23.3-2940-20:018", "23.3-2940-20", "556224-8012", "AFRY Sweden AB"),
    entry("23.3-8321-2024-001", "23.3-8321-2024", "556958-4401", "Castra Group AB"),
)


def agreement(value: str, raw: str, page: int = 1, role: FactRole = FactRole.SELF) -> Fact:
    return Fact(
        kind=FactKind.AGREEMENT_NUMBER,
        value=value,
        raw=raw,
        rule="PROC",
        block=0,
        page=page,
        role=role,
    )


def checked(facts: list[Fact], procurement: str, card: str | None = None) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=SHA,
        title="Ramavtal" if card else "Ramavtalets huvuddokument",
        document_type=DocumentType.SUPPLIER_AGREEMENT if card else DocumentType.MAIN_DOCUMENT,
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
        title=metadata.title,
        category=None if card else "Avtal",
        agreement_number=card,
        site_updated=None,
        page_url="https://www.avropa.se/ramavtal/it-konsulttjanster",
        page_title="IT-konsulttjänster",
        page_procurement_numbers=(procurement,),
        page_period=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=tuple(facts), mentions=())
    return CheckedFile(extraction, (link,), (), "pdf", 1, ())


def check(file: CheckedFile) -> list[Finding]:
    return run(CheckContext(files=(file,), links=file.links, register=REGISTER, areas=()))


def test_a_card_stating_its_own_number_has_no_finding() -> None:
    # Step 4 on the real cover gives the key "23.3-2940-2020-033"; the link has the
    # register's spelling "23.3-2940-20:033". They are the same agreement.
    cover = find_identifiers([Block(kind=BlockKind.TEXT, text=COVER, page=1)])

    assert [fact.value for fact in cover if fact.kind is FactKind.AGREEMENT_NUMBER] == [
        "23.3-2940-2020-033"
    ]
    assert check(checked(cover, "23.3-2940-20", card="23.3-2940-20:033")) == []


def test_a_card_stating_another_agreement_quarantines_the_file() -> None:
    cover = agreement("23.3-2940-20:033", "23.3.2940-20:033")

    findings = check(checked([cover], "23.3-2940-20", card="23.3-2940-20:018"))

    assert findings == [
        Finding(
            check="agreement_number",
            severity=Severity.QUARANTINE,
            subject="23.3-2940-20:033",
            message=(
                "Dokumentet ligger i leverantörskortet för avtal 23.3-2940-20:018 men anger "
                "avtalsnumret 23.3-2940-20:033 (s. 1) som sitt eget. I registret är det "
                "avtalet med AFRY Sweden AB."
            ),
            sha256=SHA,
            agreement_number="23.3-2940-20:018",
            evidence="23.3.2940-20:033",
        )
    ]


def test_a_cited_agreement_number_is_not_the_files_own() -> None:
    cited = agreement("23.3-2940-20:033", "23.3.2940-20:033", role=FactRole.CITATION)

    assert check(checked([cited], "23.3-2940-20", card="23.3-2940-20:018")) == []
    assert check(checked([cited], "23.3-8321-2024")) == []


def test_a_file_outside_the_cards_with_a_suppliers_number_is_quarantined() -> None:
    headers = [agreement("23.3-8321-2024-001", "23.3-8321-2024-001", page=p) for p in (2, 3, 4)]

    findings = check(checked(headers, "23.3-8321-2024"))

    assert [(f.severity, f.subject, f.agreement_number, f.evidence) for f in findings] == [
        (Severity.QUARANTINE, "23.3-8321-2024-001", "23.3-8321-2024-001", "23.3-8321-2024-001")
    ]
    assert findings[0].message == (
        "Dokumentet ligger inte i något leverantörskort men anger avtalsnumret "
        "23.3-8321-2024-001 (3 gånger, först på s. 2), en enskild leverantörs avtal, som sitt "
        "eget. I registret är det avtalet med Castra Group AB."
    )


def test_an_unfilled_sequence_in_a_template_is_no_agreement_number() -> None:
    # Step 4 on the real blocks: the header gives the agreement number, the clause's
    # "23.3-8321-2024-XXX" only the procurement number and a placeholder.
    header = Block(kind=BlockKind.PAGE_HEADER, text=GENERIC_HEADER, page=2)
    clause = Block(kind=BlockKind.TEXT, text=GENERIC_CLAUSE, page=3)
    clause_facts = find_identifiers([clause])

    assert FactKind.AGREEMENT_NUMBER not in {fact.kind for fact in clause_facts}
    assert check(checked(clause_facts, "23.3-8321-2024")) == []
    assert len(check(checked(find_identifiers([header, clause]), "23.3-8321-2024"))) == 1


def test_one_finding_per_agreement_number() -> None:
    facts = [
        agreement("23.3-8321-2024-001", "23.3-8321-2024-001"),
        agreement("23.3-2940-20:033", "23.3.2940-20:033"),
        agreement("23.3-8321-2024-001", "23.3-8321-2024-001", page=2),
    ]

    findings = check(checked(facts, "23.3-8321-2024"))

    assert [f.subject for f in findings] == ["23.3-8321-2024-001", "23.3-2940-20:033"]
