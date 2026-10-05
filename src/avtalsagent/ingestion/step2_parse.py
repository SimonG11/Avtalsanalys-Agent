"""Ingestion step 2: read each downloaded file into a `ParsedDocument`.

What:
    `inspect_pages` checks every page of a PDF for a text layer.
    `parse_files` runs a `DocumentParser` on each file, adds the page check
    and stores the result as JSON next to the files, so a rerun only parses
    files that are new or were parsed by another parser version.

Why:
    The architecture plan decides per page between the text layer and OCR.
    Only 1 % of the pages in the first selection lack a text layer, so there
    is no OCR service yet (M3b); a page without text is marked `needs_ocr`
    and reported, so it is never silently treated as empty. Parsing is the
    slow part of the ingestion (a layout model looks at every page), and its
    result only depends on the file and the parser, so it is cached by the
    file's hash and the parser's name.

How:
    A page needs OCR when its text layer has fewer than `MIN_TEXT_CHARS`
    characters and images cover at least `MIN_IMAGE_SHARE` of it, which is how
    a scanned page looks. A short page without images (a cover, a signature
    page) is not flagged. Results go to `<data_dir>/parsed/<sha256>.json`; one
    file that cannot be parsed becomes a FAILED result with the reason and
    never stops the run.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pydantic import ValidationError

from avtalsagent.domain.parsed import PageInfo, ParsedDocument
from avtalsagent.ingestion.parsers.base import DocumentParser, ParseError

MIN_TEXT_CHARS = 50  # fewer characters than this counts as no text layer...
MIN_IMAGE_SHARE = 0.5  # ...when images cover at least this share of the page


@dataclass(frozen=True)
class SourceFile:
    """A downloaded file, as recorded by step 1."""

    sha256: str
    file_type: str  # "pdf" or "docx"
    path: Path


class ParseStatus(StrEnum):
    PARSED = "parsed"  # parsed in this run
    CACHED = "cached"  # parsed earlier by the same parser; the stored result is used
    FAILED = "failed"  # the parser could not read the file


@dataclass(frozen=True)
class ParseResult:
    file: SourceFile
    status: ParseStatus
    document: ParsedDocument | None
    message: str | None = None


def inspect_pages(path: Path) -> list[PageInfo]:
    """Check each page of a PDF for a text layer."""
    pdf = pdfium.PdfDocument(path)
    try:
        return [_inspect_page(pdf[index], index + 1) for index in range(len(pdf))]
    finally:
        pdf.close()


def _inspect_page(page: pdfium.PdfPage, number: int) -> PageInfo:
    text = page.get_textpage().get_text_range()
    char_count = sum(1 for char in text if not char.isspace())
    width, height = page.get_size()
    image_area = 0.0
    for image in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)):
        left, bottom, right, top = image.get_bounds()
        # Only the part of the image that is on the page counts.
        image_area += max(0.0, min(right, width) - max(left, 0.0)) * max(
            0.0, min(top, height) - max(bottom, 0.0)
        )
    image_share = image_area / (width * height) if width and height else 0.0
    needs_ocr = char_count < MIN_TEXT_CHARS and image_share >= MIN_IMAGE_SHARE
    return PageInfo(number=number, char_count=char_count, needs_ocr=needs_ocr)


def parse_files(
    files: Iterable[SourceFile], parser: DocumentParser, cache_dir: Path
) -> Iterator[ParseResult]:
    """Parse each file, or reuse the stored result if the same parser made it.

    Results are yielded one at a time, so a long run can report progress.
    """
    for file in files:
        yield _parse_one(file, parser, cache_dir)


def cache_path(cache_dir: Path, sha256: str) -> Path:
    return cache_dir / f"{sha256}.json"


def load_cached(cache_dir: Path, sha256: str) -> ParsedDocument | None:
    """The stored result for a file, or None if there is none or it cannot be read."""
    path = cache_path(cache_dir, sha256)
    if not path.is_file():
        return None
    try:
        return ParsedDocument.model_validate_json(path.read_bytes())
    except ValidationError:
        return None  # written by an older version of the model: parse again


def _parse_one(file: SourceFile, parser: DocumentParser, cache_dir: Path) -> ParseResult:
    cached = load_cached(cache_dir, file.sha256)
    if cached is not None and cached.parser == parser.name:
        return ParseResult(file, ParseStatus.CACHED, cached)
    try:
        pages = inspect_pages(file.path) if file.file_type == "pdf" else []
        blocks = parser.parse(file.path)
    except (ParseError, pdfium.PdfiumError, OSError) as error:
        return ParseResult(file, ParseStatus.FAILED, None, str(error))
    document = ParsedDocument(
        sha256=file.sha256,
        file_type=file.file_type,
        parser=parser.name,
        pages=tuple(pages),
        blocks=tuple(blocks),
    )
    target = cache_path(cache_dir, file.sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_text(document.model_dump_json(), encoding="utf-8")
    partial.replace(target)
    return ParseResult(file, ParseStatus.PARSED, document)
