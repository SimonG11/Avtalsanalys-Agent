"""Read an uploaded file into sections, within the limits, or refuse it.

What:
    `parse_upload(data, filename, limits)` gives a `ParsedFile`: the file's
    kind, pages, characters, sections and warnings for the user. A file
    that is not read raises `UploadRejected` with the status and Swedish
    text the route answers with. `UploadLimits` holds the limits, from the
    settings (UPLOAD_MAX_BYTES, UPLOAD_MAX_PAGES, UPLOAD_MAX_CHARACTERS).

Why:
    The file comes from a browser and can be anything, so every step has a
    limit that is checked before the work it bounds: the bytes before
    anything is read, the type from the content (`file_type.py`), a Word
    archive's unpacked size before it is opened, a PDF's pages before their
    text is read, and the characters before the text is cut into sections.
    The function is pure and synchronous: the route runs it in a worker
    thread with a time limit, so a slow file never holds up the agent's
    runs.

How:
    `detect_kind`, then the kind's reader in `extract.py`, then
    `split_upload` (`sections.py`). A file a third-party reader fails on
    in some other way (python-docx on a broken archive, say) is 422 with
    the text for an unreadable file; the error is logged. PDF pages without
    text are named in a warning, since the agent does not see them.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from avtalsagent.config import Settings
from avtalsagent.domain.parsed import Block
from avtalsagent.domain.uploads import UploadKind, UploadSection
from avtalsagent.uploads.errors import (
    EMPTY,
    NO_TEXT,
    TOO_LARGE,
    TOO_MUCH_TEXT,
    UNREADABLE,
    UploadRejected,
)
from avtalsagent.uploads.extract import docx_blocks, pdf_blocks, text_blocks
from avtalsagent.uploads.file_type import detect_kind
from avtalsagent.uploads.sections import split_upload

_log = logging.getLogger(__name__)

EMPTY_PAGES = (
    "Sidorna {pages} har ingen text som går att läsa (de kan vara inskannade). "
    "Agenten ser inte vad som står på dem."
)
EMPTY_PAGE = (
    "Sidan {pages} har ingen text som går att läsa (den kan vara inskannad). "
    "Agenten ser inte vad som står på den."
)
# Pages named in the warning; the rest are counted.
_NAMED_PAGES = 10


@dataclass(frozen=True)
class UploadLimits:
    max_bytes: int
    max_pages: int
    max_characters: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "UploadLimits":
        return cls(
            max_bytes=settings.upload_max_bytes,
            max_pages=settings.upload_max_pages,
            max_characters=settings.upload_max_characters,
        )


@dataclass(frozen=True)
class ParsedFile:
    """What a file holds: its kind, pages (PDF), characters, sections and warnings."""

    kind: UploadKind
    pages: int | None
    characters: int
    sections: tuple[UploadSection, ...]
    warnings: tuple[str, ...]


def parse_upload(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    """The file's sections, or `UploadRejected` (see the module)."""
    if not data:
        raise UploadRejected(422, EMPTY)
    if len(data) > limits.max_bytes:
        raise UploadRejected(413, TOO_LARGE.format(limit=megabytes(limits.max_bytes)))
    kind = detect_kind(data, filename)
    pages: int | None = None
    warnings: list[str] = []
    try:
        if kind is UploadKind.PDF:
            blocks, pages, empty = pdf_blocks(data, limits.max_pages)
            if empty:
                warnings.append(_empty_pages_warning(empty))
        elif kind is UploadKind.DOCX:
            blocks = docx_blocks(data)
        else:
            blocks = text_blocks(data, markdown=filename.lower().endswith((".md", ".markdown")))
    except UploadRejected:
        raise
    except Exception:
        # A hostile or broken file can make a third-party reader fail in any way.
        _log.warning("could not read the uploaded %s file %r", kind.value, filename, exc_info=True)
        raise UploadRejected(422, UNREADABLE) from None
    _check_characters(blocks, limits.max_characters)
    sections = split_upload(blocks, kind)
    if not sections:
        raise UploadRejected(422, NO_TEXT)
    return ParsedFile(
        kind=kind,
        pages=pages,
        characters=sum(len(section.text) for section in sections),
        sections=tuple(sections),
        warnings=tuple(warnings),
    )


def _check_characters(blocks: Sequence[Block], max_characters: int) -> None:
    characters = sum(len(block.text) for block in blocks)
    if characters == 0:
        raise UploadRejected(422, NO_TEXT)
    if characters > max_characters:
        raise UploadRejected(
            413,
            TOO_MUCH_TEXT.format(characters=thousands(characters), limit=thousands(max_characters)),
        )


def _empty_pages_warning(pages: Sequence[int]) -> str:
    if len(pages) == 1:
        return EMPTY_PAGE.format(pages=pages[0])
    if len(pages) > _NAMED_PAGES:
        named = ", ".join(str(page) for page in pages[:_NAMED_PAGES])
        return EMPTY_PAGES.format(pages=f"{named} och {len(pages) - _NAMED_PAGES} till")
    named = ", ".join(str(page) for page in pages[:-1])
    return EMPTY_PAGES.format(pages=f"{named} och {pages[-1]}")


def megabytes(size: int) -> str:
    """A size in Swedish: 10485760 gives "10 MB"."""
    value = size / (1024 * 1024)
    return f"{value:.0f} MB" if value >= 1 else f"{size / 1024:.0f} kB"


def thousands(number: int) -> str:
    """A number with (non-breaking) spaces between thousands, as Swedish writes it: "1 500 000"."""
    return f"{number:,}".replace(",", " ")
