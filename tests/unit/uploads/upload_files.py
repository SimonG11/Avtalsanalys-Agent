"""Files to upload in the tests, built in the test: PDFs with reportlab, Word with python-docx.

What:
    `pdf_bytes(pages)` builds a PDF with one page per list of lines (an
    empty list is a page with a drawing and no text, as a scanned page);
    `docx_bytes(build)` a Word file that `build` fills in; `zip_bytes`
    any zip archive; `text_bomb_pdf(pages, lines)` a small PDF whose
    compressed pages hold `lines` lines of 100 characters each;
    `AGREEMENT_PAGES` a short agreement with numbered sections over three
    pages and an empty page.

Why:
    No binary fixtures in git: each file is made from a few readable lines,
    so a test shows what the file holds.

How:
    reportlab writes each line as text at a fixed place on the page, so
    pdfium reads the lines back in order. The text bomb is written by hand:
    one content stream, deflated, that every page shares.
"""

import io
import zipfile
import zlib
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


def text_bomb_pdf(pages: int, lines: int) -> bytes:
    """A PDF of a few kB whose every page has `lines` lines of 100 "A"s."""
    line = b"(" + b"A" * 100 + b") Tj T*\n"
    content = b"BT /F1 1 Tf 1 TL 0 0 Td\n" + line * lines + b"ET\n"
    stream = zlib.compress(content, 9)
    font = 3 + pages
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count %d /Kids [%s] >>"
        % (pages, b" ".join(b"%d 0 R" % (3 + n) for n in range(pages))),
        *(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (font + 1, font)
            for _ in range(pages)
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
