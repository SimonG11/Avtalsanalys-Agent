"""Render the fictional example contract to a PDF, for the demo's upload.

What:
    `python examples/uppladdning/render_pdf.py OUT.pdf` writes
    `exempelavtal-it-konsult.md` (next to this script) as a PDF with a text
    layer: the title, the note that it is fictional, and the numbered
    clauses as headings with their paragraphs. `render(markdown, out)` does
    it for any Markdown of that simple shape.

Why:
    The demo uploads a PDF, as a user would, but the repository keeps no
    PDFs (they are built, never committed), and the contract's text is
    easier to review and change as Markdown. reportlab is a dev dependency,
    already used to build the PDFs of the upload tests.

How:
    "# " is the title, "## " a heading, and the lines between blank lines
    one paragraph, wrapped by reportlab. The standard Helvetica font covers
    Swedish letters. A path inside the repository is refused, so the PDF is
    not committed by mistake.
"""

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent.parent
CONTRACT = HERE / "exempelavtal-it-konsult.md"


def render(markdown: str, out: Path) -> None:
    """Write `markdown` (title, headings, paragraphs) to `out` as a PDF."""
    styles = getSampleStyleSheet()
    story: list[Flowable] = []
    for block in markdown.strip().split("\n\n"):
        text = " ".join(line.strip() for line in block.splitlines())
        if text.startswith("## "):
            story.append(Paragraph(escape(text[3:]), styles["Heading2"]))
        elif text.startswith("# "):
            story.append(Paragraph(escape(text[2:]), styles["Title"]))
        else:
            story.append(Paragraph(escape(text), styles["BodyText"]))
            story.append(Spacer(1, 4))
    document = SimpleDocTemplate(
        str(out), pagesize=A4, title="Avropsavtal om IT-konsulttjänster (fiktivt exempel)"
    )
    document.build(story)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Skriv exempelavtalet som PDF.")
    parser.add_argument("out", type=Path, help="PDF-filen som skrivs, utanför repot")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.is_relative_to(REPOSITORY):
        sys.exit(f"Skriv PDF:en utanför repot, inte till {out}.")
    out.parent.mkdir(parents=True, exist_ok=True)
    render(CONTRACT.read_text(encoding="utf-8"), out)
    print(out)


if __name__ == "__main__":
    main()
