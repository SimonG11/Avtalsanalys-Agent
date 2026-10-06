"""Tests for avtalsagent.ingestion.checks.still_published.

The file and pages are real, from the pilot: 3117fd65796c, the template
"Utkast till personuppgiftsbiträdesavtal", linked from the pages of ITK 2020
and ITK 2024 (M4 survey, doctypes.md §9). Every pilot page is still listed on
avropa.se; the pages that are no longer listed, and the dates, are made up.
"""

from datetime import UTC, datetime

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Severity,
)
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.still_published import SUBJECT, run

SHA = "3117fd65796cd788f279e27c902940554e3b24ae9b350483dbe4f34a33d6ad02"
ITK2 = "IT-konsulttjänster 2. Ledning av IT-projekt"
ITK1 = "IT-konsulttjänster 1. Verksamhetens IT-behov"
MISSED = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)


def link(page_title: str, missing_since: datetime | None) -> CatalogLink:
    return CatalogLink(
        sha256=SHA,
        url="https://www.avropa.se/globalassets/utkast-till-personuppgiftsbitradesavtal.docx",
        title="Utkast till personuppgiftsbiträdesavtal",
        category="Stöddokument och länkar",
        agreement_number=None,
        site_updated=None,
        page_url=f"https://www.avropa.se/ramavtal/{page_title}",
        page_title=page_title,
        page_procurement_numbers=("23.3-2940-20",),
        page_period=None,
        page_missing_since=missing_since,
    )


def check(*links: CatalogLink) -> list[Finding]:
    metadata = DocumentMetadata(
        sha256=SHA,
        title="Utkast till personuppgiftsbiträdesavtal",
        document_type=DocumentType.TEMPLATE,
        type_rule="R10",
        agreement_number=None,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=None,
        is_template=True,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=(), mentions=())
    file = CheckedFile(extraction, links, (), "docx", 0, ())
    return run(CheckContext(files=(file,), links=links, register=(), areas=()))


def test_a_file_on_listed_pages_has_no_finding() -> None:
    assert check(link(ITK2, None), link(ITK1, None)) == []


def test_a_file_one_listed_page_still_links_to_stays() -> None:
    assert check(link(ITK2, MISSED), link(ITK1, None)) == []


def test_a_file_no_listed_page_links_to_is_held_back() -> None:
    assert check(link(ITK2, MISSED)) == [
        Finding(
            check="still_published",
            severity=Severity.QUARANTINE,
            subject=SUBJECT,
            message=(
                "Ingen sida som avropa.se listar länkar längre till dokumentet: sidan "
                '"IT-konsulttjänster 2. Ledning av IT-projekt" (saknas sedan 2026-10-01) finns '
                "inte längre i listan över ramavtal."
            ),
            sha256=SHA,
        )
    ]


def test_every_missing_page_is_named() -> None:
    later = datetime(2026, 10, 3, tzinfo=UTC)

    findings = check(link(ITK2, MISSED), link(ITK2, MISSED), link(ITK1, later))

    assert findings[0].message == (
        "Ingen sida som avropa.se listar länkar längre till dokumentet: sidorna "
        '"IT-konsulttjänster 2. Ledning av IT-projekt" (saknas sedan 2026-10-01) och '
        '"IT-konsulttjänster 1. Verksamhetens IT-behov" (saknas sedan 2026-10-03) finns inte '
        "längre i listan över ramavtal."
    )
