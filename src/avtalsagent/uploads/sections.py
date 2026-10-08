"""An uploaded file's blocks cut into sections, by the ingestion's rules, none too long.

What:
    `split_upload(blocks, kind)` gives the `UploadSection`s of a file: one
    per numbered heading ("6.2 Ansvar"), or per heading without a number
    when the file has no numbered outline, plus the text before the first
    heading; a section longer than `MAX_SECTION_CHARS` is cut into pieces.

Why:
    The agent reads and cites a file by its sections, as it does the
    agreements, and the user's file is often an agreement too: cut by the
    same rules (`ingestion/step3_chunk.split_sections` and the outline
    search of `ingestion/headings.py`), "punkt 6.2" of the user's file and
    of the framework agreement are comparable units. A file without
    headings is one section, and a section can be long; the agent reads a
    whole section at a time, so no piece is longer than about 2,000 words.

How:
    The blocks go to `split_sections` as a parsed document of their kind
    (a .docx as "docx", so Word's rules apply; a PDF's blocks are its text
    lines, which also lets step 3 remove running headers and page
    numbers). A PDF section's lines are joined by single line breaks again,
    since each line was a block. A long section is cut at a blank line, a
    line break or a space, as late as fits; its pieces keep the number and
    get "(del 2 av 3)" after the title. The page a piece starts on is the
    first page from the previous piece's on whose text layer has the
    piece's first line; when none has (the cut fell inside a line), the
    previous piece's page.
"""

from collections import defaultdict
from collections.abc import Sequence

from avtalsagent.domain.parsed import Block, ParsedDocument, Section
from avtalsagent.domain.uploads import UploadKind, UploadSection
from avtalsagent.ingestion.step3_chunk import split_sections

# About 2,000 words: a long section is cut into pieces of at most this many characters.
MAX_SECTION_CHARS = 12_000
_SEPARATORS = ("\n\n", "\n", " ")


def split_upload(
    blocks: Sequence[Block], kind: UploadKind, max_chars: int = MAX_SECTION_CHARS
) -> list[UploadSection]:
    """The file's sections in order, positions from 0 (see the module)."""
    document = ParsedDocument(
        sha256="", file_type=kind.value, parser="upload", pages=(), blocks=tuple(blocks)
    )
    _, sections = split_sections(document)
    pages = _pages_of_lines(blocks) if kind is UploadKind.PDF else {}
    result: list[UploadSection] = []
    for section in sections:
        text = section.text.replace("\n\n", "\n") if kind is UploadKind.PDF else section.text
        pieces = _pieces(text, max_chars)
        page = section.page_start
        for count, piece in enumerate(pieces, start=1):
            if count > 1 and page is not None:
                page = _page_of(piece, pages, page, section.page_end or page)
            title = section.title if len(pieces) == 1 else _piece_title(section, count, pieces)
            result.append(
                UploadSection(
                    position=len(result),
                    number=section.number,
                    title=title,
                    level=section.level,
                    page_start=page,
                    text=piece,
                )
            )
    return result


def _piece_title(section: Section, count: int, pieces: Sequence[str]) -> str:
    return f"{section.title} (del {count} av {len(pieces)})"


def _pieces(text: str, max_chars: int) -> list[str]:
    """`text` cut into pieces of at most `max_chars`, each at the latest separator that fits."""
    pieces: list[str] = []
    rest = text.strip()
    while len(rest) > max_chars:
        cut = max_chars
        for separator in _SEPARATORS:
            found = rest.rfind(separator, max_chars // 2, max_chars)
            if found > 0:
                cut = found
                break
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def _pages_of_lines(blocks: Sequence[Block]) -> dict[str, list[int]]:
    """Each PDF line, in the form `_key` gives, and the pages it is on, in order."""
    pages: defaultdict[str, list[int]] = defaultdict(list)
    for block in blocks:
        if block.page is not None:
            pages[_key(block.text)].append(block.page)
    return pages


def _page_of(piece: str, pages: dict[str, list[int]], start: int, end: int) -> int:
    """The first page in [start, end] with the piece's first line, else `start`."""
    first_line = piece.split("\n", 1)[0]
    return next((p for p in pages.get(_key(first_line), []) if start <= p <= end), start)


def _key(line: str) -> str:
    return " ".join(line.split())
