"""The text of an uploaded file as blocks: PDF with pypdfium2, Word with python-docx, text.

What:
    `pdf_blocks(data, max_pages, max_characters)` gives one block per line
    of a PDF's text layer, with its page, and the pages without text.
    `docx_blocks(data, max_characters)` gives a Word file's paragraphs and
    tables in order, its headings marked with their level.
    `text_blocks(data, markdown)` gives a text file's paragraphs, with each
    line that looks like a heading as a block of its own. All raise
    `UploadRejected` for a file they cannot read.

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
    A small file can hold far more text than its size suggests (a page's
    text is compressed), so the PDF and Word readers stop as soon as the
    text passes `max_characters` instead of reading the rest.

How:
    PDF: pypdfium2 opens the bytes; more than `max_pages` pages is 413
    before any text is read, and the pages are read one at a time until the
    text passes `max_characters` (413). pdfium is not thread-safe, so one
    lock lets one thread at a time use it. A page with fewer than
    `MIN_PAGE_CHARS` characters (spaces not counted) has no text; when every
    page is like that the file is refused as scanned (422). pdfium gives a
    hyphen at a line's end as U+FFFE, which is put back as "-", so a quote
    of the printed words matches. Word: python-docx, whose XML parser does
    not resolve entities; a body of more than `MAX_DOCX_BLOCKS` parts is 413
    before it is read. Word's own heading styles ("Heading 2", "Rubrik 2")
    give the level, list styles a list item, a table one block with a row
    per line; the styles' names are looked up once per file. Headers,
    footers, footnotes and text boxes are left out. Text: UTF-8 (a byte
    order mark is allowed), split at blank lines; in Markdown, "## Rubrik"
    is a heading of level 2. In all three, a line such as "§ 3 Avgifter" or
    "Bilaga 2 Prislista" is a heading of level 1, which step 3 uses when the
    file has no numbered outline.
"""

import io
import re
import threading

import docx
import pypdfium2 as pdfium
from docx.document import Document as WordDocument
from docx.enum.style import WD_STYLE_TYPE
from docx.table import Table
from docx.text.paragraph import Paragraph

from avtalsagent.domain.parsed import CELL_SEPARATOR, Block, BlockKind
from avtalsagent.ingestion.headings import is_number_alone, starts_with_number
from avtalsagent.uploads.errors import (
    NOT_UTF8,
    SCANNED,
    TOO_MANY_PAGES,
    TOO_MANY_PARAGRAPHS,
    TOO_MUCH_TEXT_OVER,
    UNREADABLE,
    UploadRejected,
    thousands,
)

# Characters, spaces not counted, a PDF page needs to count as having text: a scanned page
# can carry a stamped page number.
MIN_PAGE_CHARS = 20
# Paragraphs, tables and other parts of a Word file's body. A contract of 300 pages has a few
# thousand; reading one costs time, so a file with far more is refused before it is read.
MAX_DOCX_BLOCKS = 50_000

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


def pdf_blocks(
    data: bytes, max_pages: int, max_characters: int
) -> tuple[list[Block], int, list[int]]:
    """A PDF's text lines as blocks, its page count and the 1-based pages without text."""
    blocks: list[Block] = []
    empty: list[int] = []
    characters = 0
    with _PDFIUM:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            raise UploadRejected(422, UNREADABLE) from None
        try:
            page_count = len(pdf)
            if page_count > max_pages:
                raise UploadRejected(413, TOO_MANY_PAGES.format(pages=page_count, limit=max_pages))
            for number in range(1, page_count + 1):
                text = _page_text(pdf, number - 1)
                if sum(1 for char in text if not char.isspace()) < MIN_PAGE_CHARS:
                    empty.append(number)
                    continue
                for line in text.splitlines():
                    if line := " ".join(line.split()):
                        blocks.append(_line_block(line, number))
                        characters += len(line)
                # The pages after this one are not read: a small file can hold far more text.
                if characters > max_characters:
                    raise UploadRejected(
                        413, TOO_MUCH_TEXT_OVER.format(limit=thousands(max_characters))
                    )
        finally:
            pdf.close()
    if not blocks:
        raise UploadRejected(422, SCANNED)
    return blocks, page_count, empty


def _page_text(pdf: pdfium.PdfDocument, index: int) -> str:
    try:
        text: str = pdf[index].get_textpage().get_text_range()
    except pdfium.PdfiumError:
        raise UploadRejected(422, UNREADABLE) from None
    # pdfium marks a hyphen at a line's end with U+FFFE and joins the lines; the page shows "-".
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufffe", "-")


def docx_blocks(data: bytes, max_characters: int) -> list[Block]:
    """A Word file's paragraphs and tables as blocks, in order (see the module)."""
    document: WordDocument = docx.Document(io.BytesIO(data))
    count = len(document.element.body)
    if count > MAX_DOCX_BLOCKS:
        limit = MAX_DOCX_BLOCKS
        raise UploadRejected(
            413, TOO_MANY_PARAGRAPHS.format(count=thousands(count), limit=thousands(limit))
        )
    styles = _paragraph_style_names(document)
    blocks: list[Block] = []
    characters = 0
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            block = _table_block(item)
        else:
            block = _paragraph_block(item, styles.get(item._p.style, styles[None]))
        if block is not None:
            blocks.append(block)
            characters += len(block.text)
            if characters > max_characters:
                raise UploadRejected(
                    413, TOO_MUCH_TEXT_OVER.format(limit=thousands(max_characters))
                )
    return blocks


def _paragraph_style_names(document: WordDocument) -> dict[str | None, str]:
    """The name `paragraph.style` would give for each style id, the default's under None.

    python-docx's `paragraph.style` searches the styles for every paragraph,
    and walks them all for a paragraph without one, so a file with many
    paragraphs and styles took minutes. As there, an id that is not a
    paragraph style's gives the default.
    """
    styles = document.styles
    default = styles.default(WD_STYLE_TYPE.PARAGRAPH)
    names: dict[str | None, str] = {None: (default.name or "") if default is not None else ""}
    for style in styles:
        if style.style_id is not None and style.style_id not in names:
            paragraph = style.type == WD_STYLE_TYPE.PARAGRAPH
            names[style.style_id] = (style.name or "") if paragraph else names[None]
    return names


def _paragraph_block(paragraph: Paragraph, style: str) -> Block | None:
    text = paragraph.text.strip()
    if not text:
        return None
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
