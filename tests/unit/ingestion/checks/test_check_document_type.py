"""Tests for avtalsagent.ingestion.checks.document_type.

The links are real, from the pilot: 19c85c74c3b2
"Nuts 2 indelning", listed under "Avtal" on the four Bemanningstjänster pages;
83b9c9db99bf "Finansiella villkor vid köp av hårdvara som tjänst" under "Avtal" on
"IT-drift Större, fler än 200 anställda"; 3f646362f5b7 "Bilaga 1 Kontaktuppgifter"
under "Avtal" on "Volymavtal för Microsoft". The types come from step 4's own
rules (`extract/document_type.classify`). The untyped link "Leverantörsförteckning"
under "Övrigt" is made up: every pilot link is typed.
"""

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Severity,
)
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.document_type import run
from avtalsagent.ingestion.extract.document_type import classify

BEMANNING = (
    "Bemanningstjänster - IT-tjänster upp till 1000 timmar",
    "Bemanningstjänster - IT-tjänster överstigande 1000 timmar",
    "Bemanningstjänster - Kontorstjänster upp till 1000 timmar",
    "Bemanningstjänster - Kontorstjänster överstigande 1000 timmar",
)


def link(sha256: str, title: str, category: str | None, page_title: str, file: str) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/bilagor/1.-aktuella-rao/{file}",
        title=title,
        category=category,
        agreement_number=None,
        site_updated=None,
        page_url=f"https://www.avropa.se/ramavtal/{page_title}",
        page_title=page_title,
        page_procurement_numbers=("23.3-14537-2023",),
        page_period=None,
    )


def checked(*links: CatalogLink) -> CheckedFile:
    """A file typed by step 4's rules from its links."""
    document_type, rule = classify(links)
    metadata = DocumentMetadata(
        sha256=links[0].sha256,
        title=links[0].title,
        document_type=document_type,
        type_rule=rule,
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
    extraction = DocumentExtraction(metadata=metadata, facts=(), mentions=())
    return CheckedFile(extraction, links, (), "pdf", 1, ())


def check(*files: CheckedFile) -> list[Finding]:
    links = tuple(link for file in files for link in file.links)
    return run(CheckContext(files=files, links=links, register=(), areas=()))


NUTS = "19c85c74c3b2611ef1ff925e479cf2458d4848643cd54c2bf85a6928a7e37ccb"
NUTS_FILE = "bemanningstjanster-2023/1.-avropsstod/nuts-2-indelning-scb2.pdf"
FINANCE = "83b9c9db99bfb9d297a71d5d967b69b61ffdb3b3cc616ecec19441b62f98912b"
FINANCE_FILE = "it-drift2/7.finansiella-villkor-vid-kop-av-hardvara-som-tjanst.pdf"
CONTACTS = "3f646362f5b7e84757499950f3e514f1061a2f9692b6b9c6c23ce818ad69049b"


def test_a_file_typed_by_the_heading_of_its_links_gets_a_note() -> None:
    nuts = checked(
        *(link(NUTS, "Nuts 2 indelning", "Avtal", page, NUTS_FILE) for page in BEMANNING)
    )

    assert (nuts.metadata.document_type, nuts.metadata.type_rule) == (DocumentType.ANNEX, "F1")
    assert check(nuts) == [
        Finding(
            check="document_type",
            severity=Severity.NOTE,
            subject="Nuts 2 indelning",
            message=(
                'Ingen typregel känner igen länktiteln "Nuts 2 indelning". Dokumentet fick '
                'typen bilaga från rubriken "Avtal" som länkarna står under (regel F1).'
            ),
            sha256=NUTS,
            evidence="Nuts 2 indelning",
        )
    ]


def test_one_note_per_file() -> None:
    title = "Finansiella villkor vid köp av hårdvara som tjänst"
    finance = checked(link(FINANCE, title, "Avtal", "IT-drift Större", FINANCE_FILE))
    nuts = checked(link(NUTS, "Nuts 2 indelning", "Avtal", BEMANNING[0], NUTS_FILE))

    findings = check(finance, nuts)

    assert [(f.severity, f.sha256, f.subject) for f in findings] == [
        (Severity.NOTE, FINANCE, title),
        (Severity.NOTE, NUTS, "Nuts 2 indelning"),
    ]
    assert findings[0].message.endswith("som länken står under (regel F1).")


def test_a_file_typed_by_a_keyword_rule_has_no_finding() -> None:
    page = "Volymavtal för Microsoft"
    contacts = checked(
        link(CONTACTS, "Bilaga 1 Kontaktuppgifter", "Avtal", page, "bilaga-1_kontaktuppgifter.pdf")
    )

    assert contacts.metadata.type_rule == "R14"
    assert check(contacts) == []


def test_a_file_no_rule_types_is_reported() -> None:
    unknown = checked(
        link(NUTS, "Leverantörsförteckning", "Övrigt", BEMANNING[0], "leverantorer.pdf")
    )

    assert unknown.metadata.document_type is DocumentType.UNKNOWN
    assert check(unknown) == [
        Finding(
            check="document_type",
            severity=Severity.REPORT,
            subject="Leverantörsförteckning",
            message=(
                'Ingen typregel känner igen länktiteln "Leverantörsförteckning", listad under '
                '"Övrigt", så dokumentets typ är okänd. Det räknas som stöddokument, inte som '
                "en del av avtalet, tills en regel ger det en typ."
            ),
            sha256=NUTS,
            evidence="Leverantörsförteckning",
        )
    ]


def test_an_untyped_link_without_a_heading() -> None:
    unknown = checked(link(NUTS, "Leverantörsförteckning", None, BEMANNING[0], "leverantorer.pdf"))

    assert check(unknown)[0].message.startswith(
        'Ingen typregel känner igen länktiteln "Leverantörsförteckning", så dokumentets typ'
    )
