"""Read the framework-agreement pages on avropa.se.

What:
    `parse_index` lists the agreement pages from the A-Ö index
    (avropa.se/ramavtal/ramavtal-a-o/). `parse_agreement_page` reads one
    agreement page: its title, procurement number(s), agreement period and the
    documents it links to, both for the whole area and per supplier.

Why:
    avropa.se has no API for the documents, so the pages are the source. Keeping
    the HTML reading in one module of pure functions means a change in the
    site's layout is fixed in one place, and the tests can use saved pages
    without network access.

How:
    BeautifulSoup reads the HTML. The facts box has one `h2.fakta-rubrik` per
    fact ("Avtalsperiod", "Ramavtalsnummer", ...) with the value next to it.
    Documents are listed as `div.tbl-row` rows, each with a "Senast uppdaterad"
    date. Rows for the whole area sit under `div.tbl-label` headings ("Avtal",
    "Upphandling", ...). Rows for one supplier sit in the supplier's card
    (`li.contact-card`), whose header says "Avtal: <agreement number>". Only
    links to uploaded files count as documents. Most point to
    /globalassets/...file.pdf?v=<version>, and the version is kept separately;
    some (e.g. the Microsoft volume agreement) point to /contentassets/... and
    have no version.
"""

import re
from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from avtalsagent.domain.documents import AgreementPage, DocumentLink
from avtalsagent.domain.identifiers import IdentifierError, parse_agreement_number

# Every agreement page lives under this path; the A-Ö index links to all of them.
AGREEMENT_PAGE_PATH = "/ramavtal/ramavtalsomraden/"

# Uploaded files live under these paths; other links go to pages or other sites.
_FILE_PATHS = ("/globalassets/", "/contentassets/")

# Procurement numbers as written on the pages: 23.3-5890-2023, 23.3-2965-20, 6765/05.
_PROCUREMENT_NUMBER = re.compile(r"\d+\.\d+-\d+-\d{4}|\d+\.\d+-\d+-\d{2}\b|\b\d+/\d{2}\b")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The agreement number in a supplier card header: "Avtal: 23.3-14537-2023-014".
_CARD_AGREEMENT = re.compile(r"Avtal:\s*(\S+)")


class PageLayoutError(ValueError):
    """Raised when a page does not have the layout this module expects."""


def parse_index(html: str, base_url: str) -> list[str]:
    """Return the absolute URLs of all agreement pages in the A-Ö index, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.find_all("a", href=True):
        href = str(link["href"])
        if AGREEMENT_PAGE_PATH in href:
            url = _without_query(urljoin(base_url, href))
            if url not in urls:
                urls.append(url)
    if not urls:
        raise PageLayoutError(f"no agreement pages found in the index {base_url}")
    return urls


def parse_agreement_page(html: str, url: str) -> AgreementPage:
    """Read title, procurement numbers, period and document links from one page."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    if heading is None:
        raise PageLayoutError(f"{url} has no h1 heading")
    facts = _facts(soup)
    documents = _documents(soup, url)
    return AgreementPage(
        url=url,
        title=heading.get_text(" ", strip=True),
        procurement_numbers=_procurement_numbers(facts.get("Ramavtalsnummer", ""), soup),
        agreement_period=facts.get("Avtalsperiod") or None,
        documents=tuple(documents),
    )


def _procurement_numbers(fact: str, soup: BeautifulSoup) -> tuple[str, ...]:
    """Procurement numbers from the "Ramavtalsnummer" fact and from the supplier cards.

    Most pages state the number as a fact. Some (the IBM and Microsoft volume
    agreements) only give it in the supplier card ("Avtal: 6765/05"), so the
    cards are read too. A page with neither (e.g. a landing page that links
    to sub-pages) gets an empty tuple.
    """
    numbers = list(_PROCUREMENT_NUMBER.findall(fact))
    for card in soup.select("li.contact-card"):
        agreement = _card_agreement_number(card)
        if agreement is None:
            continue
        try:
            procurement = parse_agreement_number(agreement).procurement_number
        except IdentifierError:
            continue
        if procurement not in numbers:
            numbers.append(procurement)
    return tuple(numbers)


def _facts(soup: BeautifulSoup) -> dict[str, str]:
    """Map each fact heading to the text next to it, e.g. "Ramavtalsnummer" -> "23.3-5890-2023"."""
    facts: dict[str, str] = {}
    for heading in soup.select("h2.fakta-rubrik"):
        name = heading.get_text(" ", strip=True)
        box = heading.parent
        if box is None or name in facts:
            continue
        value = box.get_text(" ", strip=True).removeprefix(name).strip()
        facts[name] = value
    return facts


def _documents(soup: BeautifulSoup, page_url: str) -> list[DocumentLink]:
    """List the document rows in page order, with their category or supplier agreement."""
    links: list[DocumentLink] = []
    seen: set[str] = set()
    category: str | None = None
    for element in soup.select("div.tbl-label, div.tbl-row"):
        if "tbl-label" in (element.get("class") or []):
            category = element.get_text(" ", strip=True) or None
            continue
        anchor = element.find("a", href=True)
        if not isinstance(anchor, Tag):
            continue
        card = element.find_parent("li", class_="contact-card")
        link = _document_link(
            anchor,
            element,
            category=None if card else category,
            agreement_number=_card_agreement_number(card) if card else None,
            page_url=page_url,
        )
        if link is not None and link.url not in seen:
            seen.add(link.url)
            links.append(link)
    return links


def _card_agreement_number(card: Tag) -> str | None:
    header = card.find(class_="contact-header")
    match = _CARD_AGREEMENT.search(header.get_text(" ", strip=True)) if header else None
    return match.group(1) if match else None


def _document_link(
    anchor: Tag,
    row: Tag,
    category: str | None,
    agreement_number: str | None,
    page_url: str,
) -> DocumentLink | None:
    absolute = urljoin(page_url, str(anchor["href"]))
    parts = urlsplit(absolute)
    file_name = parts.path.rsplit("/", 1)[-1]
    if not parts.path.startswith(_FILE_PATHS) or "." not in file_name:
        return None  # a link to a page or another site, not to an uploaded file
    versions = parse_qs(parts.query).get("v")
    updated = _ISO_DATE.search(row.get_text(" ", strip=True))
    return DocumentLink(
        url=_without_query(absolute),
        version=versions[0] if versions else None,
        title=anchor.get_text(" ", strip=True),
        category=category,
        agreement_number=agreement_number,
        file_type=file_name.rsplit(".", 1)[-1].lower(),
        site_updated=date.fromisoformat(updated.group()) if updated else None,
    )


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
