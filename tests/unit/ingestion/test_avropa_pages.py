"""Tests for avtalsagent.ingestion.avropa_pages, on saved pages from avropa.se (no network)."""

from datetime import date
from pathlib import Path

import pytest

from avtalsagent.ingestion.avropa_pages import (
    PageLayoutError,
    parse_agreement_page,
    parse_index,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "avropa"
INDEX_URL = "https://www.avropa.se/ramavtal/ramavtal-a-o/"
PAGE_URL = (
    "https://www.avropa.se/ramavtal/ramavtalsomraden/konsulttjanster---it-och-management/"
    "it-konsulttjanster/it-konsulttjanster-2.-ledning-av-it-projekt"
)
FILES = "https://www.avropa.se/globalassets/bilagor/1.-aktuella-rao/itk-2020"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_index_lists_agreement_pages_as_absolute_urls() -> None:
    urls = parse_index(read("index.html"), INDEX_URL)

    assert urls == [
        "https://www.avropa.se/ramavtal/ramavtalsomraden/mobler-och-inredning/"
        "mobler-och-inredning/arbetsplats-och-forvaring/",
        "https://www.avropa.se/ramavtal/ramavtalsomraden/mobler-och-inredning/"
        "mobler-och-inredning/arbetsstolar/",
        "https://www.avropa.se/ramavtal/ramavtalsomraden/mobler-och-inredning/"
        "mobler-och-inredning/arkiv-och-magasin/",
    ]


def test_index_without_agreement_pages_is_an_error() -> None:
    with pytest.raises(PageLayoutError):
        parse_index("<html><body><a href='/Nyheter/'>Nyheter</a></body></html>", INDEX_URL)


def test_page_facts_are_read() -> None:
    page = parse_agreement_page(read("agreement_page.html"), PAGE_URL)

    assert page.title == "IT-konsulttjänster 2. Ledning av IT-projekt"
    assert page.procurement_numbers == ("23.3-2940-20",)  # older format, two-digit year
    assert page.agreement_period == "2022-12-01 - 2026-11-30"


def test_supplier_documents_get_the_agreement_number_from_the_card() -> None:
    page = parse_agreement_page(read("agreement_page.html"), PAGE_URL)

    supplier_documents = [d for d in page.documents if d.agreement_number is not None]

    assert [(d.agreement_number, d.title) for d in supplier_documents] == [
        ("23.3-2940-20:018", "Ramavtal"),
        ("23.3-2940-20:010", "Ramavtal"),
    ]
    chas = supplier_documents[1]
    assert chas.url == (
        f"{FILES}/itk-2.-ledning-och-it-projekt/ramavtal-per-leverantor/"
        "ramavtal-ledning-av-it-projekt-chas.pdf"
    )
    assert chas.version == "8dacebd4d74fd80"  # from "?v=", not part of the url
    assert chas.category is None
    assert chas.site_updated == date(2022, 11, 25)


def test_area_documents_get_the_category_they_are_listed_under() -> None:
    page = parse_agreement_page(read("agreement_page.html"), PAGE_URL)

    area_documents = [(d.category, d.title) for d in page.documents if d.agreement_number is None]

    assert area_documents == [
        ("Stöddokument och länkar", "Vägledning IT-konsulttjänster"),
        ("Stöddokument och länkar", "Snabbguide"),
        ("Upphandling", "Ansökningsinbjudan"),
        ("Upphandling", "Anbudsinbjudan"),
        ("Upphandling", "Frågor och svar - Upphandlingsdokument"),
    ]


def test_links_to_pages_are_not_documents() -> None:
    page = parse_agreement_page(read("agreement_page.html"), PAGE_URL)

    # The fixture links to e.g. /innehall/it-konsulttjanster--1-5/... pages.
    assert all("/globalassets/" in d.url or "/contentassets/" in d.url for d in page.documents)
    assert {d.file_type for d in page.documents} == {"pdf"}


def test_procurement_number_can_come_from_the_supplier_card_only() -> None:
    # The Microsoft volume agreement page has no "Ramavtalsnummer" fact.
    html = """
    <html><body><h1>Volymavtal för Microsoft</h1>
    <ul><li class="item content-area contact-card">
      <div class="contact-header"><h2>Microsoft AB</h2><span>Avtal: 23.5-3718-2024</span></div>
    </li></ul></body></html>
    """

    page = parse_agreement_page(html, PAGE_URL)

    assert page.procurement_numbers == ("23.5-3718-2024",)
    assert page.documents == ()


def test_page_without_heading_is_an_error() -> None:
    with pytest.raises(PageLayoutError):
        parse_agreement_page("<html><body><p>Sidan finns inte</p></body></html>", PAGE_URL)
