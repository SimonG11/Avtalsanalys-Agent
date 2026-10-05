"""The content of a document after parsing (step 2) and splitting (step 3).

What:
    `ParsedDocument`: a file read by a parser, as a list of `Block`s (heading,
    paragraph, list item, table, page header, ...) in reading order, plus one
    `PageInfo` per PDF page saying whether the page has a text layer.
    `Section`: one numbered section of a document ("14.2 Leverantörens
    uppsägning") with its text and its place in the table of contents.
    `Chunk`: a piece of a section that is small enough to search, with a
    context header that says where it comes from.

Why:
    The steps after parsing must not depend on Docling's own document model,
    so the parser can be swapped (ADR 0001, `DocumentParser`) without touching
    the splitter. Agreements are cited by section number ("punkt 14.2"), so
    the section, not a fixed number of characters, is the unit the agent reads
    and cites. Long sections are also split into chunks for search
    (parent-child): a chunk is found, the whole section is read.

How:
    Plain Pydantic models without behaviour beyond small helpers. A parser in
    `ingestion/parsers/` produces the blocks, `ingestion/step2_parse.py` adds
    the page check and builds `ParsedDocument`, and `ingestion/step3_chunk.py`
    builds `Section` and `Chunk`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# Between the cells of a table row: "Konsult nivå 3 | 1 150 kr".
CELL_SEPARATOR = " | "


class BlockKind(StrEnum):
    TITLE = "title"  # the document title
    HEADING = "heading"  # marked as a heading by the parser (layout model or Word style)
    TEXT = "text"  # a paragraph, caption or footnote
    LIST_ITEM = "list_item"
    TABLE = "table"  # cells as text, one row per line, separated by CELL_SEPARATOR
    TOC = "toc"  # marked as a table of contents by the parser
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"


class Block(BaseModel):
    """One piece of text in reading order."""

    model_config = ConfigDict(frozen=True)

    kind: BlockKind
    text: str
    page: int | None  # 1-based page number; None for Word files, which have no pages
    level: int | None = None  # heading level, when the source has them (Word heading styles)


class PageInfo(BaseModel):
    """What step 2 found on one PDF page."""

    model_config = ConfigDict(frozen=True)

    number: int  # 1-based
    char_count: int  # characters in the text layer, whitespace not counted
    # The page is an image with (almost) no text layer, e.g. a scanned signed
    # appendix. Its text is missing until it is read with OCR.
    needs_ocr: bool


class ParsedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str  # the file's hash, as in source_document
    file_type: str  # "pdf" or "docx"
    parser: str  # which parser and version produced it, e.g. "docling 2.133.0"
    pages: tuple[PageInfo, ...]  # empty for Word files
    blocks: tuple[Block, ...]

    @property
    def pages_needing_ocr(self) -> list[int]:
        return [page.number for page in self.pages if page.needs_ocr]


class Section(BaseModel):
    """A numbered section, or the text before the first heading."""

    model_config = ConfigDict(frozen=True)

    position: int  # 0-based order in the document
    number: (
        str | None
    )  # "14.2"; None for the text before the first heading or an unnumbered heading
    title: str  # the heading without its number, e.g. "Leverantörens uppsägning"
    level: int  # 1 for "14", 2 for "14.2"; 0 for the text before the first heading
    parent: int | None  # position of the enclosing section, e.g. "14" for "14.2"
    path: tuple[str, ...]  # the headings from the top down to this one, with numbers
    page_start: int | None
    page_end: int | None
    text: str  # the heading line and the body, with blocks separated by blank lines

    @property
    def heading(self) -> str:
        """The heading as shown in a table of contents, e.g. "14.2 Leverantörens uppsägning"."""
        return f"{self.number} {self.title}" if self.number else self.title


class Chunk(BaseModel):
    """A piece of a section, the unit that is searched."""

    model_config = ConfigDict(frozen=True)

    section: int  # position of the section it belongs to
    position: int  # 0-based order within the section
    # Where the text comes from, e.g. "IT-drift › Allmänna villkor › 14 Uppsägning".
    # It is put in front of the text when the chunk is indexed (contextual retrieval).
    context_header: str
    text: str
