"""Check the sections of step 3 against the PDF's own text layer.

What:
    `check_lines` sorts the lines of a PDF's text layer into those a section
    contains, those only in text step 3 removes on purpose, and those found
    nowhere. `outline_gaps` lists lines that start with a section number that
    fits the outline (its parent and the number before it are sections) but is
    not a section itself. For a questions-and-answers log, `question_lines`
    counts its entries in the text layer, to compare with its sections.
    `check_document` runs the checks for one parsed PDF.

Why:
    `contents_missing` (step 3) checks a document against its own table of
    contents, but half of the files have none. The text layer is a second
    source that does not depend on the layout model: a line it has and no
    section has was lost on the way, and a numbered line between two sections
    is probably a heading the outline missed (IBM's "1.7 Gällande lagar", which
    the layout model ran into the paragraph before it). A part that ends up in
    the wrong section keeps all its lines, so that kind of error is not seen
    here. `python -m avtalsagent.ingestion verify` runs the checks on all PDFs.

How:
    The text layer is read with pypdfium2, page by page. Lines are compared
    after normalisation (lower case, letters and digits only, so spaces and
    hyphens at line ends drop out), since the layout model joins lines that
    the text layer breaks. Lines with fewer than `MIN_LINE_CHARS` letters and
    digits (page numbers, single words) are skipped. Lines in the blocks step 3
    removes on purpose (page headers and footers, the table of contents, an
    e-signature certificate) are counted apart, not as missing. Word files have
    no text layer of their own and are not checked here.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from avtalsagent.domain.parsed import ParsedDocument, Section
from avtalsagent.ingestion.headings import split_number
from avtalsagent.ingestion.step3_chunk import (
    OutlineKind,
    body_blocks,
    is_question_line,
    split_sections,
)

MIN_LINE_CHARS = 25  # letters and digits, after normalisation
_NOT_LETTER_OR_DIGIT = re.compile(r"[^0-9a-zåäöéü]+")


@dataclass(frozen=True)
class TextLayerLine:
    page: int  # 1-based
    text: str


@dataclass(frozen=True)
class LineCheck:
    checked: int  # text-layer lines long enough to be checked
    removed: int  # ...found only in the text step 3 removes on purpose
    missing: list[TextLayerLine]  # ...found nowhere in the parsed document


@dataclass(frozen=True)
class DocumentCheck:
    sha256: str
    outline: OutlineKind
    lines: LineCheck
    gaps: list[TextLayerLine]  # numbered lines that fit the outline but are not sections
    questions: int | None  # entries of a questions-and-answers log in the text layer
    question_sections: int | None  # ...and the sections step 3 made of them


def text_layer(path: Path) -> list[list[str]]:
    """The lines of each page's text layer."""
    pdf = pdfium.PdfDocument(path)
    try:
        return [
            page.get_textpage()
            .get_text_range()
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .split("\n")
            for page in pdf
        ]
    finally:
        pdf.close()


def check_document(document: ParsedDocument, pages: Sequence[Sequence[str]]) -> DocumentCheck:
    """Run the checks on a parsed PDF, given the lines of its text layer."""
    outline, sections = split_sections(document)
    lines = check_lines(document, sections, pages)
    questions = question_sections = None
    if outline is OutlineKind.QUESTIONS:
        questions = question_lines(pages)
        question_sections = sum(1 for section in sections if section.level > 0)
    gaps = outline_gaps(sections, pages) if outline is OutlineKind.NUMBERED else []
    return DocumentCheck(document.sha256, outline, lines, gaps, questions, question_sections)


def normalise(text: str) -> str:
    """Lower case, letters and digits only."""
    return _NOT_LETTER_OR_DIGIT.sub("", text.lower())


def check_lines(
    document: ParsedDocument, sections: Sequence[Section], pages: Sequence[Sequence[str]]
) -> LineCheck:
    """Where each text-layer line ended up: in a section, removed on purpose, or nowhere."""
    found = normalise("".join(section.text for section in sections))
    kept = {id(block) for block in body_blocks(document)}
    removed_text = normalise("".join(b.text for b in document.blocks if id(b) not in kept))
    checked = removed = 0
    missing: list[TextLayerLine] = []
    for page, lines in enumerate(pages, start=1):
        for line in lines:
            text = normalise(line)
            if len(text) < MIN_LINE_CHARS:
                continue
            checked += 1
            if text in found:
                continue
            if text in removed_text:
                removed += 1
            else:
                missing.append(TextLayerLine(page, line.strip()))
    return LineCheck(checked, removed, missing)


def question_lines(pages: Sequence[Sequence[str]]) -> int:
    """The entries of a questions-and-answers log in its text layer ("12 Publik fråga")."""
    return sum(1 for lines in pages for line in lines if is_question_line(line))


def outline_gaps(
    sections: Sequence[Section], pages: Sequence[Sequence[str]]
) -> list[TextLayerLine]:
    """Numbered text-layer lines that fit between the sections but are not one.

    A line "1.7 Gällande lagar" fits when 1 and 1.6 are sections (or, for x.1,
    when x is). Numbers on the top level are not checked: numbered lists start
    there. Each number is reported once.
    """
    numbers = {section.number for section in sections if section.number}
    gaps: list[TextLayerLine] = []
    reported: set[str] = set()
    for page, lines in enumerate(pages, start=1):
        for line in lines:
            split = split_number(line)
            if split is None or len(split[0]) < 2 or not split[1][:1].isupper():
                continue
            number, _ = split
            name = ".".join(map(str, number))
            parent = ".".join(map(str, number[:-1]))
            before = ".".join(map(str, (*number[:-1], number[-1] - 1)))
            if (
                name not in numbers
                and name not in reported
                and parent in numbers
                and (number[-1] == 1 or before in numbers)
            ):
                gaps.append(TextLayerLine(page, line.strip()))
                reported.add(name)
    return gaps
