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
    PDFs: Docling's standard pipeline with the Heron layout model run through
    ONNX Runtime on the CPU, so PyTorch is not needed. OCR is off (step 2
    reports pages without a text layer instead) and the table-structure model
    is off, since it needs PyTorch. A table without structure has no cells in
    Docling, so its text is read from the PDF's text layer inside the table's
    box, one line per row. Word files: Docling's Word reader, which needs no
    model and keeps heading levels and heading numbers. See ADR 0008.
"""

from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.object_detection_engine_options import (
    OnnxRuntimeObjectDetectionEngineOptions,
)
from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
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
BLOCK_MAPPING_VERSION = 1

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


class DoclingParser:
    """A `DocumentParser` for PDF and Word files."""

    def __init__(self) -> None:
        layout = LayoutObjectDetectionOptions.from_preset(LAYOUT_PRESET)
        layout.engine_options = OnnxRuntimeObjectDetectionEngineOptions()
        pdf_options = PdfPipelineOptions(do_ocr=False, do_table_structure=False)
        pdf_options.layout_options = layout
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)},
        )
        self._name = (
            f"docling {version('docling-slim')} ({LAYOUT_PRESET}, onnx, no ocr), "
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
    for item, _depth in document.iterate_items(included_content_layers=layers):
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
        text = text.strip()
        if not text:
            continue
        # Word headings have real levels (Heading 1, 2, ...). The PDF layout model
        # does not know levels and calls every heading level 1, so none is given.
        level = item.level if isinstance(item, SectionHeaderItem) and pdf is None else None
        blocks.append(Block(kind=kind, text=text, page=page, level=level))
    return blocks


def _inside_table(item: NodeItem, document: DoclingDocument) -> bool:
    """True for text in a table cell; it is already part of the table's text."""
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
            for cell in row:
                text = " ".join(cell.text.split())
                # A cell spanning several columns appears once per column.
                if text and (not cells or cells[-1] != text):
                    cells.append(text)
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)
    if pdf is None:
        return ""
    # No structure (the table model is off): read the text inside the table's box.
    parts = []
    for prov in table.prov:
        page = pdf[prov.page_no - 1]
        height = document.pages[prov.page_no].size.height
        box = prov.bbox.to_bottom_left_origin(height)
        text_page = page.get_textpage()
        parts.append(text_page.get_text_bounded(box.l, box.b, box.r, box.t))
    lines = (line.strip() for part in parts for line in part.splitlines())
    return "\n".join(line for line in lines if line)
