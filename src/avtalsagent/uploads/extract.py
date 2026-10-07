"""The text of an uploaded file as blocks: PDF with pypdfium2, Word with python-docx, text.

What:
    `pdf_blocks(data, max_pages)` gives one block per line of a PDF's text
    layer, with its page, and the pages without text. `docx_blocks(data)`
    gives a Word file's paragraphs and tables in order, its headings marked
    with their level. `text_blocks(data, markdown)` gives a text file's
    paragraphs, with each line that looks like a heading as a block of its
    own. All raise `UploadRejected` for a file they cannot read.

Why:
    The blocks are what step 3 of the ingestion splits into numbered
    sections (`ingestion/step3_chunk.py`), so an uploaded file is cut by
    the same rules as the agreements (`uploads/sections.py`). The ingestion
    reads a PDF with Docling's layout model, which needs PyTorch, its
    models and seconds per page; the API reads the text layer instead,
    which takes milliseconds per page and needs nothing more in the API's
    process. The text layer has no headings marked, but the numbered
    headings are found from the numbers (`ingestion/headings.py`), which is
    what decides the sections there too. A PDF without a text layer
    (scanned) has nothing to read and is refused; reading it would need OCR.

How:
    PDF: pypdfium2 opens the bytes; more than `max_pages` pages is 413
    before any text is read. pdfium is not thread-safe, so one lock lets one
    thread at a time use it. A page with fewer than `MIN_PAGE_CHARS`
    characters (spaces not counted) has no text; when every page is like
    that the file is refused as scanned (422). Word: python-docx, whose XML
    parser does not resolve entities; Word's own heading styles ("Heading
    2", "Rubrik 2") give the level, list styles a list item, a table one
    block with a row per line. Headers, footers, footnotes and text boxes
    are left out. Text: UTF-8 (a byte order mark is allowed), split at blank
    lines; in Markdown, "## Rubrik" is a heading of level 2. In all three,
    a line such as "§ 3 Avgifter" or "Bilaga 2 Prislista" is a heading of
    level 1, which step 3 uses when the file has no numbered outline.
"""

import io
import re
import threading

import docx
import pypdfium2 as pdfium
from docx.document import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from avtalsagent.domain.parsed import CELL_SEPARATOR, Block, BlockKind
from avtalsagent.ingestion.headings import is_number_alone, starts_with_number
from avtalsagent.uploads.errors import (
    NOT_UTF8,
    SCANNED,
    TOO_MANY_PAGES,
    UNREADABLE,
    UploadRejected,
)

# Characters, spaces not counted, a PDF page needs to count as having text: a scanned page
# can carry a stamped page number.
MIN_PAGE_CHARS = 20

# A heading without a section number: "§ 3", "§ 3 Avgifter", "Bilaga 2 – Prislista". The
# title must start with a capital letter, so a wrapped line ("Bilaga 2 och 3 ska ...") is not.
_OTHER_HEADING = re.compile(
    r"^(?:§\s*\d{1,3}[a-z]?|(?:[Bb]ilaga|BILAGA)\s+\d{1,3}[a-z]?)"
    r"(?:[\s.:–-]+[A-ZÅÄÖ\"”(].*)?$"
)
_OTHER_HEADING_MAX_CHARS = 100
_MARKDOWN_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>\S.*?)\s*#*$")
_WORD_HEADING = re.compile(r"^(?:heading|rubrik)\s*(?P<level>\d)$", re.IGNORECASE)
_WORD_TITLE = {"title", "rubrik"}

# pdfium may be used by one thread at a time (pypdfium2's documentation).
_PDFIUM = threading.Lock()


def pdf_blocks(data: bytes, max_pages: int) -> tuple[list[Block], int, list[int]]:
    """A PDF's text lines as blocks, its page count and the 1-based pages without text."""
    with _PDFIUM:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            raise UploadRejected(422, UNREADABLE) from None
        try:
            page_count = len(pdf)
            if page_count > max_pages:
                raise UploadRejected(413, TOO_MANY_PAGES.format(pages=page_count, limit=max_pages))
            pages = [_page_text(pdf, index) for index in range(page_count)]
        finally:
            pdf.close()
    blocks: list[Block] = []
    empty: list[int] = []
    for number, text in enumerate(pages, start=1):
        if sum(1 for char in text if not char.isspace()) < MIN_PAGE_CHARS:
            empty.append(number)
            continue
        for line in text.splitlines():
            if line := " ".join(line.split()):
                blocks.append(_line_block(line, number))
    if not blocks:
        raise UploadRejected(422, SCANNED)
    return blocks, page_count, empty


def _page_text(pdf: pdfium.PdfDocument, index: int) -> str:
    try:
        text: str = pdf[index].get_textpage().get_text_range()
    except pdfium.PdfiumError:
        raise UploadRejected(422, UNREADABLE) from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def docx_blocks(data: bytes) -> list[Block]:
    """A Word file's paragraphs and tables as blocks, in order (see the module)."""
    document: WordDocument = docx.Document(io.BytesIO(data))
    blocks: list[Block] = []
    for item in document.iter_inner_content():
        block = _table_block(item) if isinstance(item, Table) else _paragraph_block(item)
        if block is not None:
            blocks.append(block)
    return blocks


def _paragraph_block(paragraph: Paragraph) -> Block | None:
    text = paragraph.text.strip()
    if not text:
        return None
    style = (paragraph.style.name or "") if paragraph.style is not None else ""
    if match := _WORD_HEADING.match(style.strip()):
        return Block(kind=BlockKind.HEADING, text=text, page=None, level=int(match["level"]))
    if style.strip().lower() in _WORD_TITLE:
        return Block(kind=BlockKind.TITLE, text=text, page=None)
    if "list" in style.lower():
        return Block(kind=BlockKind.LIST_ITEM, text=text, page=None)
    return _line_block(text, None)


def _table_block(table: Table) -> Block | None:
    rows = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = " ".join(cell.text.split())
            # A merged cell is given once for each column it spans.
            if text and (not cells or cells[-1] != text):
                cells.append(text)
        if cells:
            rows.append(CELL_SEPARATOR.join(cells))
    return Block(kind=BlockKind.TABLE, text="\n".join(rows), page=None) if rows else None


def text_blocks(data: bytes, *, markdown: bool) -> list[Block]:
    """A text file's paragraphs as blocks; a heading-like line is a block of its own."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadRejected(422, NOT_UTF8) from None
    blocks: list[Block] = []
    paragraph: list[str] = []

    def end_paragraph() -> None:
        if paragraph:
            blocks.append(_line_block("\n".join(paragraph), None))
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.strip()
        heading = _MARKDOWN_HEADING.match(line) if markdown else None
        if heading is not None:
            end_paragraph()
            level = len(heading["marks"])
            blocks.append(
                Block(kind=BlockKind.HEADING, text=heading["title"], page=None, level=level)
            )
        elif not line:
            end_paragraph()
        elif _starts_section(line):
            end_paragraph()
            blocks.append(_line_block(line, None))
        else:
            paragraph.append(line)
    end_paragraph()
    return blocks


def _starts_section(line: str) -> bool:
    """Whether a line looks like a heading: "6.2 Ansvar", "4.", "§ 3 Avgifter", "Bilaga 2"."""
    return starts_with_number(line) or is_number_alone(line) or _is_other_heading(line)


def _is_other_heading(line: str) -> bool:
    return len(line) <= _OTHER_HEADING_MAX_CHARS and _OTHER_HEADING.match(line) is not None


def _line_block(text: str, page: int | None) -> Block:
    """A block of body text, or a heading of level 1 when it is a "§ 3" or "Bilaga 2" line."""
    if _is_other_heading(text):
        return Block(kind=BlockKind.HEADING, text=text, page=page, level=1)
    return Block(kind=BlockKind.TEXT, text=text, page=page)
