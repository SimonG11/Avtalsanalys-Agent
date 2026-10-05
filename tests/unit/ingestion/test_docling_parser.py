"""Tests for avtalsagent.ingestion.parsers.docling_parser.

Word files need no model. PDFs need Docling's layout model, which is downloaded
from Hugging Face on first use; where it cannot be downloaded the PDF tests are
skipped with the reason, unless AVTALSAGENT_REQUIRE_MODELS=1 (set in CI), which
makes them fail instead.
"""

import os
from pathlib import Path

import pytest
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.parsers.docling_parser import DoclingParser


@pytest.fixture(scope="module")
def parser() -> DoclingParser:
    return DoclingParser()


def make_docx(target: Path) -> Path:
    document = Document()
    document.add_heading("1 Personuppgiftsbiträdesavtalets syfte", level=1)
    document.add_heading("1.1 Detta personuppgiftsbiträdesavtal reglerar behandlingen.", level=2)
    document.add_paragraph("Personuppgiftsansvarig modifierar och anpassar utkastet.")
    document.add_paragraph("Behandlingen avser kunddata", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Personuppgiftsansvarig:"
    table.cell(0, 1).text = "Myndigheten"
    table.cell(1, 0).text = "Organisationsnummer:"
    table.cell(1, 1).text = "202100-0829"
    document.add_heading("2 Parter", level=1)
    document.save(str(target))
    return target


def test_word_headings_keep_numbers_and_levels(parser: DoclingParser, tmp_path: Path) -> None:
    blocks = parser.parse(make_docx(tmp_path / "pub.docx"))
    headings = [(b.text, b.level) for b in blocks if b.kind is BlockKind.HEADING]
    assert headings == [
        ("1 Personuppgiftsbiträdesavtalets syfte", 1),
        ("1.1 Detta personuppgiftsbiträdesavtal reglerar behandlingen.", 2),
        ("2 Parter", 1),
    ]
    assert all(b.page is None for b in blocks)


def test_a_word_table_is_one_block_with_one_line_per_row(
    parser: DoclingParser, tmp_path: Path
) -> None:
    blocks = parser.parse(make_docx(tmp_path / "pub.docx"))
    tables = [b.text for b in blocks if b.kind is BlockKind.TABLE]
    assert tables == ["Personuppgiftsansvarig: | Myndigheten\nOrganisationsnummer: | 202100-0829"]
    # The cell text is not repeated as paragraphs after the table.
    assert not any(b.text == "Myndigheten" for b in blocks if b.kind is not BlockKind.TABLE)


def test_word_text_and_list_items(parser: DoclingParser, tmp_path: Path) -> None:
    blocks = parser.parse(make_docx(tmp_path / "pub.docx"))
    kinds = {b.text: b.kind for b in blocks}
    assert kinds["Personuppgiftsansvarig modifierar och anpassar utkastet."] is BlockKind.TEXT
    assert any(
        b.kind is BlockKind.LIST_ITEM and b.text.endswith("Behandlingen avser kunddata")
        for b in blocks
    )


def make_pdf(target: Path) -> Path:
    canvas = Canvas(str(target), pagesize=A4)
    _, height = A4
    for page in (1, 2):
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(72, height - 72, f"6.{page} Försäkring och ansvar del {page}")
        canvas.setFont("Helvetica", 11)
        for line in range(12):
            canvas.drawString(
                72,
                height - 110 - line * 16,
                f"Ramavtalsleverantören ska ha en ansvarsförsäkring, stycke {page}.{line}.",
            )
        canvas.setFont("Helvetica", 8)
        canvas.drawString(72, 40, f"Sida {page} (2)")
        canvas.showPage()
    canvas.save()
    return target


@pytest.fixture(scope="module")
def pdf_blocks(parser: DoclingParser, tmp_path_factory: pytest.TempPathFactory) -> list[Block]:
    path = make_pdf(tmp_path_factory.mktemp("pdf") / "terms.pdf")
    try:
        return parser.parse(path)
    except Exception as error:  # the layout model could not be downloaded
        if os.environ.get("AVTALSAGENT_REQUIRE_MODELS") == "1":
            raise
        pytest.skip(f"Docling's layout model is not available: {error}")


def test_pdf_text_comes_from_the_text_layer_with_pages(pdf_blocks: list[Block]) -> None:
    texts = [b.text for b in pdf_blocks]
    assert any("6.1 Försäkring och ansvar del 1" in text for text in texts)
    assert any("stycke 2.11." in text for text in texts)
    pages = {b.page for b in pdf_blocks if "stycke 2." in b.text}
    assert pages == {2}


def test_pdf_page_numbers_are_not_body_text(pdf_blocks: list[Block]) -> None:
    page_numbers = [b for b in pdf_blocks if b.text.startswith("Sida ")]
    assert all(b.kind is BlockKind.PAGE_FOOTER for b in page_numbers)
