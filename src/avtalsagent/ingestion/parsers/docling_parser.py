"""Read PDF and Word files with Docling.

What:
    `DoclingParser.parse(path)` converts a file with Docling and returns its
    text as `Block`s in reading order: title, headings, paragraphs, list items,
    tables, tables of contents, page headers and page footers.

Why:
    Docling reads the PDF's own text layer, so the text is exactly what the
    document says, which the citation check needs (architecture plan, 4).
    Its layout model finds headings, lists, tables and page headers and puts
    the text in reading order, which plain text extraction does not.

How:
    PDFs: Docling's standard pipeline on the CPU, with the Heron layout model
    and the TableFormer table model. OCR is off (step 2 reports pages without
    a text layer instead). The text of every block, table cells included,
    comes from the PDF's text layer. Three changes to what Docling gives:
    a hyphen at the end of a line is kept ("2025-" + "08-19" stays a date),
    text inside a region the layout model calls a picture is kept (TendSign
    draws its question boxes as graphics), and a table without cells is read
    from the text layer inside its box, one line per row.
    Word files: Docling's Word reader, which needs no model and keeps
    heading levels and heading numbers. See ADR 0008.
"""

from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.models.stages.page_assemble.page_assemble_model import (
    PageAssembleModel,
    PageAssembleOptions,
)
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling_core.types.doc import (
    ContentLayer,
    DocItemLabel,
    DoclingDocument,
    ListItem,
    NodeItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
)

from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.parsers.base import ParseError

LAYOUT_PRESET = "layout_heron_default"
# Raised when the mapping from Docling's items to blocks changes, so step 2 parses
# every file again instead of reusing results made by the old mapping.
BLOCK_MAPPING_VERSION = 2

# Docling labels that are read as plain text. Pictures, formulas and form
# fields are left out: without OCR they carry no text.
_TEXT_LABELS = {
    DocItemLabel.TEXT,
    DocItemLabel.PARAGRAPH,
    DocItemLabel.CAPTION,
    DocItemLabel.FOOTNOTE,
    DocItemLabel.CODE,
    DocItemLabel.REFERENCE,
    DocItemLabel.HANDWRITTEN_TEXT,
}
_KIND_BY_LABEL = {
    DocItemLabel.TITLE: BlockKind.TITLE,
    DocItemLabel.SECTION_HEADER: BlockKind.HEADING,
    DocItemLabel.LIST_ITEM: BlockKind.LIST_ITEM,
    DocItemLabel.PAGE_HEADER: BlockKind.PAGE_HEADER,
    DocItemLabel.PAGE_FOOTER: BlockKind.PAGE_FOOTER,
    DocItemLabel.DOCUMENT_INDEX: BlockKind.TOC,
    DocItemLabel.TABLE: BlockKind.TABLE,
} | dict.fromkeys(_TEXT_LABELS, BlockKind.TEXT)


class _KeepHyphensAssembleModel(PageAssembleModel):
    """Docling's page assembly, but a hyphen at the end of a line is kept.

    Docling drops a hyphen between two words at a line break, to join a
    hyphenated word. In these documents a hyphen at a line break is almost
    always part of the text: "IT-" + "konsulttjänster", "2025-" + "08-19",
    "nivå 1-" + "4". Dropping it changes dates and ranges, so the lines are
    joined here and Docling only normalises the joined text (ligatures,
    quotation marks).
    """

    def sanitize_text(self, lines: list[str]) -> str:
        joined = ""
        for line in lines:
            hyphenated = len(joined) > 1 and joined.endswith("-") and joined[-2].isalnum()
            joined += line if not joined or hyphenated else " " + line
        return super().sanitize_text([joined])


class _KeepHyphensPdfPipeline(StandardPdfPipeline):
    """The standard PDF pipeline with `_KeepHyphensAssembleModel`."""

    def _init_models(self) -> None:
        super()._init_models()
        self.assemble_model = _KeepHyphensAssembleModel(options=PageAssembleOptions())


class DoclingParser:
    """A `DocumentParser` for PDF and Word files."""

    def __init__(self) -> None:
        pdf_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
        pdf_options.layout_options = LayoutObjectDetectionOptions.from_preset(LAYOUT_PRESET)
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=_KeepHyphensPdfPipeline, pipeline_options=pdf_options
                )
            },
        )
        self._name = (
            f"docling {version('docling-slim')} ({LAYOUT_PRESET}, tableformer, no ocr), "
            f"blocks v{BLOCK_MAPPING_VERSION}"
        )

    @property
    def name(self) -> str:
        return self._name

    def parse(self, path: Path) -> list[Block]:
        result = self._converter.convert(path, raises_on_error=False)
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            reasons = "; ".join(error.error_message for error in result.errors)
            raise ParseError(f"docling could not convert {path.name}: {reasons or result.status}")
        if path.suffix.lower() == ".pdf":
            pdf = pdfium.PdfDocument(path)
            try:
                return _blocks(result.document, pdf)
            finally:
                pdf.close()
        return _blocks(result.document, None)


def _blocks(document: DoclingDocument, pdf: pdfium.PdfDocument | None) -> list[Block]:
    blocks: list[Block] = []
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    # The layout model sometimes calls a box drawn around text a picture (TendSign's
    # question boxes: "Accepterar anbudsgivaren villkoren? Ja/Nej. Ja krävs"). Its
    # text items are the picture's children, so pictures are traversed too.
    items = document.iterate_items(included_content_layers=layers, traverse_pictures=True)
    for item, _depth in items:
        kind = _KIND_BY_LABEL.get(getattr(item, "label", None))  # type: ignore[arg-type]
        if kind is None or _inside_table(item, document):
            continue
        page = item.prov[0].page_no if getattr(item, "prov", None) else None  # type: ignore[attr-defined]
        if isinstance(item, TableItem):
            text = _table_text(item, document, pdf)
        elif isinstance(item, ListItem) and item.marker:
            text = f"{item.marker} {item.text}"
        elif isinstance(item, TextItem):
            text = item.text
        else:
            continue
        if pdf is None and item.content_layer is ContentLayer.FURNITURE:
            # A Word file's own headers and footers ("Sida 3 (13)"). Docling does not
            # say which of the two it is, so both become page headers.
            kind = BlockKind.PAGE_HEADER
        text = text.strip()
        if not text:
            continue
        # Word headings have real levels (Heading 1, 2, ...). The PDF layout model
        # does not know levels and calls every heading level 1, so none is given.
        level = item.level if isinstance(item, SectionHeaderItem) and pdf is None else None
        blocks.append(Block(kind=kind, text=text, page=page, level=level))
    return blocks


def _inside_table(item: NodeItem, document: DoclingDocument) -> bool:
    """True for text in a table cell; it is already part of the table's text.

    A table's caption and footnotes are its children too, but not part of
    its cells, so they are kept ("*Aktuell omfattning beskrivs i ...").
    """
    if getattr(item, "label", None) in (DocItemLabel.CAPTION, DocItemLabel.FOOTNOTE):
        return False
    parent = item.parent
    while parent is not None:
        node = parent.resolve(document)
        if isinstance(node, TableItem):
            return True
        parent = node.parent
    return False


def _table_text(table: TableItem, document: DoclingDocument, pdf: pdfium.PdfDocument | None) -> str:
    """The table's text, one row per line, with cells separated by " | "."""
    if table.data.table_cells:
        rows = []
        for row in table.data.grid:
            cells: list[str] = []
            seen: set[tuple[int, int]] = set()
            for cell in row:
                # A cell spanning several columns appears once per column. Empty
                # cells are kept, so every value stays in its column ("Ja |  | Ja").
                start = (cell.start_row_offset_idx, cell.start_col_offset_idx)
                if start not in seen:
                    seen.add(start)
                    cells.append(" ".join(cell.text.split()))
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows)
    if pdf is None:
        return ""
    # No cells (the table model found no structure): read the text inside the table's box.
    parts = []
    for prov in table.prov:
        page = pdf[prov.page_no - 1]
        height = document.pages[prov.page_no].size.height
        box = prov.bbox.to_bottom_left_origin(height)
        text_page = page.get_textpage()
        parts.append(text_page.get_text_bounded(box.l, box.b, box.r, box.t))
    lines = (line.strip() for part in parts for line in part.splitlines())
    return "\n".join(line for line in lines if line)
