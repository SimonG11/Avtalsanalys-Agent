"""Files to upload in the tests, built in the test: PDFs with reportlab, Word with python-docx.

What:
    `pdf_bytes(pages)` builds a PDF with one page per list of lines (an
    empty list is a page with a drawing and no text, as a scanned page);
    `docx_bytes(build)` a Word file that `build` fills in; `zip_bytes`
    any zip archive; `AGREEMENT_PAGES` a short agreement with numbered
    sections over three pages and an empty page.

Why:
    No binary fixtures in git: each file is made from a few readable lines,
    so a test shows what the file holds.

How:
    reportlab writes each line as text at a fixed place on the page, so
    pdfium reads the lines back in order.
"""

import io
import zipfile
from collections.abc import Callable, Sequence

import docx
from docx.document import Document as WordDocument
from reportlab.pdfgen import canvas

AGREEMENT_PAGES = [
    ["Avtal om IT-drift", "1 Parter", "Kunden och Leverantören.", "2 Avtalstid"],
    ["Avtalet gäller i två år.", "2.1 Förlängning", "Avtalet kan förlängas ett år i taget."],
    [],
    ["3 Uppsägning", "Uppsägningstiden är tre (3) månader."],
]


def pdf_bytes(pages: Sequence[Sequence[str]]) -> bytes:
    """A PDF with the lines of each page, top down; an empty page has only a rectangle."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for lines in pages:
        if not lines:
            pdf.rect(50, 50, 400, 600, fill=1)
        for row, line in enumerate(lines):
            pdf.drawString(50, 800 - 14 * row, line)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def docx_bytes(build: Callable[[WordDocument], object]) -> bytes:
    """A Word file that `build` fills in."""
    document = docx.Document()
    build(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    """A zip archive (deflated) with the given members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()
