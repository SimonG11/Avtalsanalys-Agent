"""Ingestion step 3: split each parsed document into sections and chunks.

What:
    `split_sections` turns a `ParsedDocument` into `Section`s: one per numbered
    heading ("14.2 Leverantörens uppsägning"), plus the text before the first
    heading. `chunk_sections` cuts long sections into `Chunk`s for search and
    gives each chunk a context header ("IT-drift › Allmänna villkor ›
    14 Uppsägning › 14.2 Leverantörens uppsägning"). `chunk_document` does both.

Why:
    The agent cites agreements by section number, so the section is the unit it
    reads and cites (architecture plan, 4.3). A search works best on pieces of
    a few hundred words, so a long section is cut into chunks that point back
    to it (parent-child): the chunk is found, the whole section is read. A
    chunk such as "Vitet uppgår till 5 000 kr" says nothing about which
    agreement or section it is from; the context header adds that before the
    chunk is indexed (contextual retrieval, validation document point 7),
    without a language model, so the same input always gives the same header.

How:
    1. Page headers, page footers, page numbers and lines repeated on many
       pages are removed, and so is the table of contents (it repeats the
       headings and would otherwise be found by searches).
    2. `ingestion/headings.py` finds the numbered headings. A questions-and-
       answers log is split per question instead. If a document has no usable
       numbered outline, the parser's own headings are used without numbers,
       and a document without headings becomes one section.
    3. Each heading starts a section that runs to the next heading. Its level
       comes from the number (6.21.9 is level 3) and its parent is the nearest
       section above it with a lower level.
    4. A section longer than `max_chars` is cut between blocks, and a block
       longer than that between sentences.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from avtalsagent.domain.parsed import Block, BlockKind, Chunk, ParsedDocument, Section
from avtalsagent.ingestion.headings import Heading, find_candidates, select_outline, toc_entries

MAX_CHUNK_CHARS = 1500  # about 350 words; tuned on the test set in M5
MAX_TITLE_CHARS = 120  # longer heading text is a numbered clause; its title is cut
HEADER_SEPARATOR = " › "

# A line that only says which page it is: "Sida 3 (25)", "Sida 2/30", "Page 4 of 9", "12".
_PAGE_NUMBER = re.compile(
    r"^(?:sida|sid\.?|page)?\s*\d{1,4}\s*(?:(?:/|av|of|\()\s*\d{1,4}\)?)?$", re.IGNORECASE
)
# A short line that ends with the page counter of a form or printout:
# "Utskrivet: 2021-02-09 12:21 Sida 5 av 111", "Datum Sid 2 (27)".
_PAGE_LABEL = re.compile(
    r"(?:^|\s)(?:sidan|sida|sid\.?|page)\s*\d{1,4}\s*(?:(?:/|av|of)\s*\d{1,4}|\(\d{1,4}\))$",
    re.IGNORECASE,
)
_PAGE_LABEL_MAX_CHARS = 100
_FURNITURE_KINDS = (BlockKind.PAGE_HEADER, BlockKind.PAGE_FOOTER, BlockKind.TOC)
# A short line on at least this share of the pages (and at least three) is a running
# header or footer, e.g. "23.3-5890-2023 IT-drift 2023, område Mindre".
_REPEATED_SHARE = 0.3
_REPEATED_MIN_PAGES = 3
_REPEATED_MAX_CHARS = 150
# A numbered outline is used when it has at least this many headings and the first
# one comes before this share of the document's text; otherwise the numbers are
# probably a list or a table, not the document's structure.
_MIN_NUMBERED_HEADINGS = 2
_MAX_TEXT_BEFORE_FIRST_HEADING = 0.5
_SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+(?=[A-ZÅÄÖ0-9\"”(])")
_PREAMBLE_TITLE = "Text före första rubriken"
# An entry in a TendSign "Frågor och svar" printout: "12 Publik fråga". Private
# questions and notices from the authority have no number. The questions quote
# the tender's headings ("5.6.3.1 Kvalitetsledningssystem"), so a document with
# such entries is split per entry and never by its numbers.
_QUESTION = re.compile(
    r"^(?:(?P<number>\d{1,4}) )?"
    r"(?P<title>Publik fråga|Privat fråga|Publikt informationsmeddelande)$"
)
_MIN_QUESTIONS = 3
_CONTENTS_TITLES = {"innehåll", "innehållsförteckning", "table of contents", "contents"}


class OutlineKind(StrEnum):
    NUMBERED = "numbered"  # sections from numbered headings
    QUESTIONS = "questions"  # a questions-and-answers log: one section per question
    HEADINGS = "headings"  # no numbered outline; the parser's headings without numbers
    NONE = "none"  # no headings: the whole document is one section


@dataclass(frozen=True)
class DocumentContext:
    """Where a document belongs, for the context header of its chunks."""

    agreement: str  # e.g. "IT-drift (23.3-5890-2023)"
    document: str  # e.g. "Allmänna villkor"


@dataclass(frozen=True)
class LinkInfo:
    """One link to a file on an agreement page, with the register's names."""

    title: str  # the link text, e.g. "Allmänna villkor" or "Ramavtal"
    agreement_number: str | None  # set for a supplier's own agreement
    supplier_name: str | None  # from the register, for a supplier's own agreement
    page_title: str  # e.g. "IT-drift Mindre, upp till 200 anställda"
    procurement_numbers: tuple[str, ...]  # the page's procurement numbers
    framework_areas: tuple[str, ...]  # their framework areas in the register


def document_context(links: Sequence[LinkInfo]) -> DocumentContext:
    """The agreement and document names for a file's context header.

    The agreement is the framework area in the register ("IT-drift"), with the
    procurement number when there is only one. A template linked from several
    areas names them all. The document is the link text used most often (the
    same file is sometimes linked with a typo); a supplier's own agreement
    gets its agreement number and supplier name.
    """
    areas = sorted({area for link in links for area in link.framework_areas})
    if not areas:  # a page whose procurement is not in the register
        areas = sorted({link.page_title for link in links})
    procurements = sorted({number for link in links for number in link.procurement_numbers})
    agreement = ", ".join(areas)
    if len(procurements) == 1:
        agreement += f" ({procurements[0]})"

    counts = Counter(link.title for link in links)
    document = min(counts, key=lambda title: (-counts[title], len(title), title))
    supplier = next((link for link in links if link.agreement_number), None)
    if supplier is not None:
        document += f" {supplier.agreement_number}"
        if supplier.supplier_name:
            document += f" ({supplier.supplier_name})"
    return DocumentContext(agreement=agreement, document=document)


@dataclass(frozen=True)
class ChunkedDocument:
    sha256: str
    outline: OutlineKind
    sections: list[Section]
    chunks: list[Chunk]


def chunk_document(
    document: ParsedDocument, context: DocumentContext, max_chars: int = MAX_CHUNK_CHARS
) -> ChunkedDocument:
    """Split a parsed document into sections and chunks."""
    outline, sections = split_sections(document)
    return ChunkedDocument(
        document.sha256, outline, sections, chunk_sections(sections, context, max_chars)
    )


def clean_blocks(document: ParsedDocument) -> list[Block]:
    """The blocks without page furniture and without the table of contents."""
    page_count = max((block.page or 0 for block in document.blocks), default=0)
    repeated = _repeated_lines(document.blocks, page_count)
    kept = [
        block
        for block in document.blocks
        if block.kind not in _FURNITURE_KINDS
        and not _PAGE_NUMBER.match(block.text.strip())
        and not _is_page_label(block.text)
        and _normalise(block.text) not in repeated
    ]
    toc = toc_entries(kept)
    return [block for index, block in enumerate(kept) if index not in toc]


def _is_page_label(text: str) -> bool:
    text = text.strip()
    return len(text) <= _PAGE_LABEL_MAX_CHARS and _PAGE_LABEL.search(text) is not None


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _repeated_lines(blocks: Sequence[Block], page_count: int) -> set[str]:
    if page_count < _REPEATED_MIN_PAGES:
        return set()
    pages: defaultdict[str, set[int]] = defaultdict(set)
    for block in blocks:
        if block.page is not None and len(block.text) <= _REPEATED_MAX_CHARS:
            pages[_normalise(block.text)].add(block.page)
    needed = max(_REPEATED_MIN_PAGES, _REPEATED_SHARE * page_count)
    return {text for text, seen in pages.items() if len(seen) >= needed}


def split_sections(document: ParsedDocument) -> tuple[OutlineKind, list[Section]]:
    """The document's sections in order, and how they were found."""
    blocks = clean_blocks(document)
    outline = OutlineKind.NUMBERED
    headings = _questions(blocks)
    if len(headings) >= _MIN_QUESTIONS:
        outline = OutlineKind.QUESTIONS
    elif _usable(headings := select_outline(find_candidates(blocks)), blocks):
        headings = _with_unnumbered_parts(headings, blocks)
    else:
        outline = OutlineKind.HEADINGS
        headings = [
            Heading(index, 1, None, block.level or 1, " ".join(block.text.split()))
            for index, block in enumerate(blocks)
            if block.kind is BlockKind.HEADING
        ]
        if not headings:
            outline = OutlineKind.NONE

    sections: list[Section] = []
    first = headings[0].index if headings else len(blocks)
    if any(block.text.strip() for block in blocks[:first]):
        title = next((b.text for b in blocks[:first] if b.kind is BlockKind.TITLE), None)
        sections.append(_section(0, None, title or _PREAMBLE_TITLE, 0, None, (), blocks[:first]))
    open_sections: list[Section] = []  # the current section and its ancestors
    for i, heading in enumerate(headings):
        end = headings[i + 1].index if i + 1 < len(headings) else len(blocks)
        while open_sections and open_sections[-1].level >= heading.level:
            open_sections.pop()
        parent = open_sections[-1] if open_sections else None
        number = heading.number
        title = _short_title(heading.title)
        own = f"{number} {title}" if number else title
        path = (*(parent.path if parent else ()), own)
        own_blocks = list(blocks[heading.index : end])
        if heading.consumed == 2:
            # The number stood alone on its line; join it with the title that followed.
            number_block, title_block = own_blocks[:2]
            joined = f"{number_block.text.strip()} {title_block.text.strip()}"
            own_blocks[:2] = [number_block.model_copy(update={"text": joined})]
        section = _section(
            len(sections),
            number,
            title,
            heading.level,
            parent.position if parent else None,
            path,
            own_blocks,
        )
        sections.append(section)
        open_sections.append(section)
    return outline, sections


def _questions(blocks: Sequence[Block]) -> list[Heading]:
    """The entries of a questions-and-answers log, found by their first line."""
    headings = []
    for index, block in enumerate(blocks):
        lines = block.text.strip().splitlines()
        if lines and (match := _QUESTION.match(" ".join(lines[0].split()))):
            headings.append(Heading(index, 1, match["number"], 1, match["title"]))
    return headings


def _with_unnumbered_parts(headings: list[Heading], blocks: Sequence[Block]) -> list[Heading]:
    """Add unnumbered headings at the top level of a numbered outline.

    A Word template can have a part without numbers after its numbered
    sections, e.g. "Instruktion till Personuppgiftsbiträdesavtalet" (Heading 1)
    after "16 Tvistelösning". Without this it would become part of 16.1. Only
    headings whose level is known (Word) and at least as high as the numbered
    top level count; a contents heading ("Innehåll") is skipped.
    """
    levels = [level for h in headings if (level := blocks[h.index].level) is not None]
    if not levels:
        return headings
    top = min(levels)
    taken = {h.index + offset for h in headings for offset in range(h.consumed)}
    parts = [
        Heading(index, 1, None, 1, " ".join(block.text.split()))
        for index, block in enumerate(blocks)
        if block.kind is BlockKind.HEADING
        and block.level is not None
        and block.level <= top
        and index not in taken
        and _normalise(block.text) not in _CONTENTS_TITLES
    ]
    return sorted([*headings, *parts], key=lambda heading: heading.index)


def _usable(headings: Sequence[Heading], blocks: Sequence[Block]) -> bool:
    if len(headings) < _MIN_NUMBERED_HEADINGS:
        return False
    total = sum(len(block.text) for block in blocks)
    before = sum(len(block.text) for block in blocks[: headings[0].index])
    return before <= _MAX_TEXT_BEFORE_FIRST_HEADING * total


def _short_title(title: str) -> str:
    """The title, cut at a word boundary when the heading is a whole clause."""
    if len(title) <= MAX_TITLE_CHARS:
        return title
    return title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0] + " …"


def _section(
    position: int,
    number: str | None,
    title: str,
    level: int,
    parent: int | None,
    path: tuple[str, ...],
    blocks: Sequence[Block],
) -> Section:
    pages = [block.page for block in blocks if block.page is not None]
    return Section(
        position=position,
        number=number,
        title=title,
        level=level,
        parent=parent,
        path=path,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        text="\n\n".join(block.text.strip() for block in blocks if block.text.strip()),
    )


def context_header(context: DocumentContext, section: Section) -> str:
    """The header "ramavtal › dokument › rubrikstig" from the architecture plan."""
    return HEADER_SEPARATOR.join((context.agreement, context.document, *section.path))


def chunk_sections(
    sections: Sequence[Section], context: DocumentContext, max_chars: int = MAX_CHUNK_CHARS
) -> list[Chunk]:
    """Cut each section into chunks of at most `max_chars` characters.

    A section that is only a short heading ("6 Allmänna villkor", followed
    directly by 6.1) gets no chunk; its text is in its subsections. A heading
    that is a whole clause ("3.4 Avtalet gäller i två år.") does get one.
    """
    chunks: list[Chunk] = []
    for section in sections:
        if not section.text.strip() or _heading_only(section.text):
            continue
        header = context_header(context, section)
        for position, text in enumerate(_pieces(section.text, max_chars)):
            chunks.append(
                Chunk(section=section.position, position=position, context_header=header, text=text)
            )
    return chunks


def _heading_only(text: str) -> bool:
    line = text.strip()
    return "\n" not in line and len(line) <= MAX_TITLE_CHARS and not line.endswith(".")


def _pieces(text: str, max_chars: int) -> list[str]:
    """Pack paragraphs into pieces of at most `max_chars`, splitting long paragraphs."""
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        units.extend(_split_long(paragraph, max_chars))
    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = unit
    if current:
        pieces.append(current)
    return pieces


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """Split a paragraph longer than `max_chars` between sentences, or else between words."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > max_chars:  # one very long sentence: cut between words
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            parts.append(current)
            current = sentence
    if current:
        parts.append(current)
    return parts
