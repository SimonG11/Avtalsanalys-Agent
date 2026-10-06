"""Tests for avtalsagent.ingestion.checks.procurement_number.

The numbers, pages and register rows are real, from the pilot (M4 survey,
identifiers.md §3), cited as sha[:12] and PDF page: 185c8246e536 p1 "23.3-1688-2024
IT-konsulttjänster - IT-säkerhet" (linked from IT-säkerhet, 23.3-8321-2024);
adcd1c5ed90e (Word) "diarienummer 23.3-2283-22" (Informationsförsörjning,
23.3-2649-2022); 18309f4961d3 p47 "avseende IT-drift 2023 med diarienummer
23.3-5890-2023 och är tillämpliga på Kontraktet" (IT-drift Större,
23.3-10639-2023); 54211e718d8e p2 "(dnr 23.3-7067-17)"; 4b6c2a533fae p2 "Sid 2
(27) Dnr 23.5-1688-2024"; fb9447f0b8bf p1 "96-15-2015" and "Avtal 6765/05". A file
stating the Microsoft number on the IBM page is made up.
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
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.procurement_number import (
    NumberStatus,
    number_status,
    pages_text,
    places,
    run,
)

SHA = "ab" * 32
IT_SAKERHET = ("IT-konsulttjänster 3. IT-säkerhet", "23.3-8321-2024")
INFORMATIONSFORSORJNING = ("Informationsförsörjning", "23.3-2649-2022")
IT_DRIFT_MINDRE = ("IT-drift Mindre, upp till 200 anställda", "23.3-5890-2023")
IT_DRIFT_STORRE = ("IT-drift Större, fler än 200 anställda", "23.3-10639-2023")
LEDNING = ("IT-konsulttjänster 2. Ledning av IT-projekt", "23.3-2940-20")
ARKITEKTUR = ("IT-konsulttjänster 4. Arkitektur och utveckling", "23.3-2940-20")
VERKSAMHETENS = ("IT-konsulttjänster 1. Verksamhetens IT-behov", "23.3-1688-2024")
IBM = ("Volymavtal för IBM", "6765/05")
MICROSOFT = ("Volymavtal för Microsoft", "23.5-3718-2024")


def entry(procurement: str, area: str) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=procurement,
        procurement_number=procurement,
        org_number="556026-6883",
        supplier_name="Leverantör AB",
        former_supplier_name=None,
        framework_area=area,
        sub_area_path=(area,),
        valid_from=date(2024, 5, 1),
        valid_to=date(2027, 4, 30),
        max_extension_to=None,
    )


REGISTER = (
    entry("23.3-8321-2024", "IT-konsulttjänster Resurskonsulter"),
    entry("23.3-1688-2024", "IT-konsulttjänster Resurskonsulter"),
    entry("23.3-2940-20", "IT-konsulttjänster Resurskonsulter"),
    entry("23.3-2649-2022", "Programvaror och tjänster"),
    entry("23.3-5890-2023", "IT-drift"),
    entry("23.3-10639-2023", "IT-drift"),
    entry("6765/05", "Programvaror och tjänster"),
    entry("23.5-3718-2024", "Programvaror och tjänster"),
)


def number(
    value: str,
    raw: str | None = None,
    page: int | None = 1,
    role: FactRole = FactRole.SELF,
    kind: FactKind = FactKind.PROCUREMENT_NUMBER,
) -> Fact:
    return Fact(
        kind=kind, value=value, raw=raw or value, rule="PROC", block=0, page=page, role=role
    )


def checked(facts: list[Fact], *pages: tuple[str, str]) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=SHA,
        title="Prisbilaga",
        document_type=DocumentType.PRICE_ANNEX,
        type_rule="R06",
        agreement_number=None,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=None,
        is_template=False,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    links = tuple(
        CatalogLink(
            sha256=SHA,
            url="https://www.avropa.se/globalassets/prisbilaga.pdf",
            title="Prisbilaga",
            category="Avtal",
            agreement_number=None,
            site_updated=None,
            page_url=f"https://www.avropa.se/ramavtal/{title}",
            page_title=title,
            page_procurement_numbers=(procurement,),
            page_period=None,
        )
        for title, procurement in pages
    )
    extraction = DocumentExtraction(metadata=metadata, facts=tuple(facts), mentions=())
    return CheckedFile(extraction, links, (), "pdf", 1, ())


def check(file: CheckedFile) -> tuple[list[Finding], NumberStatus]:
    context = CheckContext(files=(file,), links=file.links, register=REGISTER, areas=())
    return run(context), number_status(file, context)


def test_own_number_of_another_procurement_quarantines_the_file() -> None:
    findings, status = check(checked([number("23.3-1688-2024")], IT_SAKERHET))

    assert findings == [
        Finding(
            check="procurement_number",
            severity=Severity.QUARANTINE,
            subject="23.3-1688-2024",
            message=(
                "Diarienumret 23.3-1688-2024 (s. 1) tillhör inte upphandlingen på sidan som "
                "länkar till dokumentet (23.3-8321-2024), och dokumentet anger inget eget "
                "nummer som gör det. I registret är det en annan upphandling inom "
                "IT-konsulttjänster Resurskonsulter."
            ),
            sha256=SHA,
            evidence="23.3-1688-2024",
        )
    ]
    assert status is NumberStatus.DEVIATES


def test_own_number_not_in_the_register_quarantines_the_file() -> None:
    # A Word file: no page. The value is the key, as the register lacks the number.
    file = checked([number("23.3-2283-2022", "23.3-2283-22", page=None)], INFORMATIONSFORSORJNING)

    findings, _ = check(file)

    assert [(f.severity, f.subject, f.evidence) for f in findings] == [
        (Severity.QUARANTINE, "23.3-2283-2022", "23.3-2283-22")
    ]
    assert findings[0].message.startswith("Diarienumret 23.3-2283-2022 tillhör inte")
    assert findings[0].message.endswith("Numret finns inte i registret.")


def test_a_wrong_own_number_quarantines_also_beside_the_right_one() -> None:
    facts = [
        number("23.3-10639-2023", page=1),
        number("23.3-10639-2023", page=2),
        number("23.3-5890-2023", page=47),
    ]

    findings, status = check(checked(facts, IT_DRIFT_STORRE))

    assert [(f.severity, f.subject) for f in findings] == [(Severity.QUARANTINE, "23.3-5890-2023")]
    assert "men dokumentet anger också 23.3-10639-2023, som gör det" in findings[0].message
    assert status is NumberStatus.DEVIATES


def test_a_cited_number_of_another_procurement_is_a_note() -> None:
    facts = [
        number("23.3-2940-20", "23.3.2940-20", page=1),
        number("23.3-7067-2017", "23.3-7067-17", page=2, role=FactRole.CITATION),
    ]

    findings, status = check(checked(facts, LEDNING, ARKITEKTUR))

    assert [(f.severity, f.subject, f.evidence) for f in findings] == [
        (Severity.NOTE, "23.3-7067-2017", "23.3-7067-17")
    ]
    assert findings[0].message == (
        "Dokumentet hänvisar inom parentes till diarienummer 23.3-7067-2017 (s. 2), som inte "
        "tillhör upphandlingen på sidorna som länkar till dokumentet (23.3-2940-20). Numret "
        "finns inte i registret."
    )
    assert status is NumberStatus.DEVIATES


def test_a_cited_number_of_its_own_procurement_is_no_finding() -> None:
    cited = number("23.3-8321-2024", role=FactRole.CITATION)

    assert check(checked([cited], IT_SAKERHET)) == ([], NumberStatus.MATCHES)


def test_an_agreement_number_counts_with_its_procurement_by_key() -> None:
    # 7a49e1a61b31 p1: "Ramavtal 23.3.2940-20:033", in the register's spelling; the page
    # writes the procurement "23.3-2940-20", the key of both is 23.3-2940-2020.
    card = number("23.3-2940-20:033", "23.3.2940-20:033", kind=FactKind.AGREEMENT_NUMBER)

    assert check(checked([card], LEDNING)) == ([], NumberStatus.MATCHES)


def test_an_agreement_number_of_another_procurement_quarantines_the_file() -> None:
    card = number("23.3-2940-20:033", "23.3.2940-20:033", kind=FactKind.AGREEMENT_NUMBER)

    findings, _ = check(checked([card], VERKSAMHETENS))

    assert [(f.severity, f.subject, f.evidence) for f in findings] == [
        (Severity.QUARANTINE, "23.3-2940-20", "23.3.2940-20:033")
    ]


def test_numbers_of_case_management_are_not_compared() -> None:
    # 23.5-1688-2024 is agreement management, not procurement 23.3-1688-2024.
    header = number("23.5-1688-2024", page=2)

    assert check(checked([header], VERKSAMHETENS)) == ([], NumberStatus.CASE_MANAGEMENT_ONLY)


def test_the_old_letterhead_number_is_not_compared() -> None:
    facts = [number("96-15-2015"), number("6765/05", "6765/05")]

    assert check(checked(facts, IBM)) == ([], NumberStatus.MATCHES)


def test_a_case_management_number_the_register_has_is_compared() -> None:
    microsoft = number("23.5-3718-2024")

    assert check(checked([microsoft], MICROSOFT)) == ([], NumberStatus.MATCHES)
    findings, status = check(checked([microsoft], IBM))
    assert [(f.severity, f.subject) for f in findings] == [(Severity.QUARANTINE, "23.5-3718-2024")]
    assert status is NumberStatus.DEVIATES


def test_a_file_without_numbers_has_no_number() -> None:
    org = Fact(
        kind=FactKind.ORG_NUMBER,
        value="202100-0829",
        raw="202100-0829",
        rule="ORG",
        block=0,
        page=1,
    )

    assert check(checked([org], IT_SAKERHET)) == ([], NumberStatus.NO_NUMBER)


def test_one_finding_per_number_however_often_it_stands() -> None:
    facts = [number("23.3-1688-2024", page=page) for page in (1, 2, 3)]

    findings, _ = check(checked(facts, IT_SAKERHET))

    assert len(findings) == 1
    assert "Diarienumret 23.3-1688-2024 (3 gånger, först på s. 1) tillhör" in findings[0].message


def test_pages_text_names_each_procurement_once() -> None:
    # 4f886a784c5d (Vägledning IT-drift) is linked from both IT-drift pages; 54211e718d8e
    # from both 2940 pages.
    assert pages_text(checked([], IT_DRIFT_MINDRE, IT_DRIFT_STORRE)) == (
        "upphandlingarna på sidorna som länkar till dokumentet (23.3-5890-2023, 23.3-10639-2023)"
    )
    assert pages_text(checked([], LEDNING, ARKITEKTUR)) == (
        "upphandlingen på sidorna som länkar till dokumentet (23.3-2940-20)"
    )


def test_places_in_a_word_file_have_no_page() -> None:
    assert places([number("23.3-2283-2022", page=None)]) == ""
    assert places([number("23.3-2283-2022", page=None)] * 2) == " (2 gånger)"
