"""Check the sections of step 3 against the PDF's own text layer.

What:
    `missing_lines` lists the lines of a PDF's text layer that no section
    contains. `outline_gaps` lists lines that start with a section number that
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
    the layout model ran into the paragraph before it). These checks found the
    errors fixed in M3; `python -m avtalsagent.ingestion verify` reruns them.

How:
    The text layer is read with pypdfium2, page by page. Lines are compared
    after normalisation (lower case, letters and digits only, hyphens at line
    ends removed), since the layout model joins lines that the text layer
    breaks. Lines shorter than `MIN_LINE_CHARS` (page numbers, single words)
    are skipped, and lines in the blocks step 3 removes on purpose (page
    headers and footers, the table of contents) count as found. Word files
    have no text layer of their own and are not checked here.
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

MIN_LINE_CHARS = 25  # after normalisation
# Soft hyphens and the markers some PDF producers put where a word was hyphenated.
_INVISIBLE = re.compile("[­￾\u0002]")
_HYPHEN_AT_LINE_END = re.compile(r"-\s*\n\s*")
_NOT_LETTER_OR_DIGIT = re.compile(r"[^0-9a-zåäöéü]+")


@dataclass(frozen=True)
class TextLayerLine:
    page: int  # 1-based
    text: str


@dataclass(frozen=True)
class DocumentCheck:
    sha256: str
    outline: OutlineKind
    lines: int  # text-layer lines long enough to be checked
    missing: list[TextLayerLine]  # ...that no section contains
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
    lines, missing = missing_lines(document, sections, pages)
    questions = question_sections = None
    if outline is OutlineKind.QUESTIONS:
        questions = question_lines(pages)
        question_sections = sum(1 for section in sections if section.level > 0)
    gaps = outline_gaps(sections, pages) if outline is OutlineKind.NUMBERED else []
    return DocumentCheck(
        document.sha256, outline, lines, missing, gaps, questions, question_sections
    )


def normalise(text: str) -> str:
    text = _HYPHEN_AT_LINE_END.sub("", _INVISIBLE.sub("", text))
    return _NOT_LETTER_OR_DIGIT.sub("", text.lower())


def missing_lines(
    document: ParsedDocument, sections: Sequence[Section], pages: Sequence[Sequence[str]]
) -> tuple[int, list[TextLayerLine]]:
    """How many text-layer lines were checked, and those that no section contains."""
    found = normalise("".join(section.text for section in sections))
    kept = {id(block) for block in body_blocks(document)}
    removed = normalise("".join(b.text for b in document.blocks if id(b) not in kept))
    checked = 0
    missing: list[TextLayerLine] = []
    for page, lines in enumerate(pages, start=1):
        for line in lines:
            text = normalise(line)
            if len(text) < MIN_LINE_CHARS:
                continue
            checked += 1
            if text not in found and text not in removed:
                missing.append(TextLayerLine(page, line.strip()))
    return checked, missing


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
