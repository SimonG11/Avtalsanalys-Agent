"""The framework-agreement pages on avropa.se and the documents they link to.

What:
    `AgreementPage`: one page under avropa.se/ramavtal/ramavtalsomraden/, with
    its procurement number(s) and document links. `DocumentLink`: one link to a
    file on that page (PDF, Word, ...), either a document for the whole
    agreement area (listed under a category such as "Avtal") or a document for
    one supplier (listed in the supplier's card, with its agreement number).

Why:
    Statens inköpscentral publishes each framework agreement's documents
    (main agreement document, general terms, requirements, templates) on one
    page per agreement area. The page states the procurement number
    ("Ramavtalsnummer"), which is the same number as in the Excel register, so
    pages and documents can be tied to the register without guessing.

How:
    `ingestion/avropa_pages.py` builds these objects from the page HTML.
    `ingestion/step1_fetch.py` downloads the linked files. The parsed content
    of a file (blocks, sections, chunks) is in `domain/parsed.py`.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict


class DocumentLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Absolute URL without the query string; identifies the document.
    url: str
    # The "?v=..." value avropa.se adds to each link. It changes when the file
    # is replaced, so an unchanged value means the file need not be downloaded.
    version: str | None
    title: str  # the link text, e.g. "Ramavtalets huvuddokument"
    category: str | None  # the heading the link is listed under, e.g. "Avtal"
    # Set when the link is in a supplier's card, e.g. "23.3-5834-2022-018";
    # the same agreement number as in the Excel register.
    agreement_number: str | None
    file_type: str  # lower-case extension: "pdf", "docx", ...
    site_updated: date | None  # "Senast uppdaterad" next to the link


class AgreementPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str  # the page heading, e.g. "IT-drift Mindre, upp till 200 anställda"
    procurement_numbers: tuple[str, ...]  # from "Ramavtalsnummer", e.g. ("23.3-5890-2023",)
    agreement_period: str | None  # e.g. "2024-11-14 - 2028-11-13", as written
    documents: tuple[DocumentLink, ...]
