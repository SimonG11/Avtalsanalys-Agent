"""What an uploaded file is, from its content, and a name that is safe to store and send.

What:
    `detect_kind(data, filename)` says whether a file is a PDF, a Word file
    (.docx) or text, or refuses it (`UploadRejected`: 415 for another type,
    413 for a Word file that unpacks too large, 422 for a broken one or a
    text file that is not UTF-8).
    `safe_filename(name)` is the name without path parts or control
    characters.

Why:
    A name can say anything: a file named "avtal.pdf" can be an executable,
    and a .docx is a zip archive that can unpack to gigabytes (a zip bomb).
    The type is therefore read from the file's first bytes, and the name's
    suffix must agree with it. A Word file's archive is checked before any
    parser opens it, by the sizes its directory declares and then by
    unpacking every member a chunk at a time: Python's zipfile unpacks a
    deflated member in one call of up to 1 GiB before it cuts the data to
    the declared size, and a bzip2 or LZMA member with no bound at all, so
    a member that lies about its size would fill the memory when
    python-docx reads it. Unpacked a megabyte at a time, it is refused as
    soon as it passes its declared size. The name is shown
    in the web app and sent back in a Content-Disposition header, where a
    path ("../../etc/passwd"), a line break or a right-to-left override
    could mislead or break the header.

How:
    A PDF has "%PDF-" in its first kilobyte (readers accept some bytes
    before it), a .docx is a zip archive (bytes 50 4B 03 04) with
    `word/document.xml`, and text is a file named .txt or .md without NUL
    bytes in its first 8 KiB (whether it is UTF-8 is checked when it is
    read; one with NUL bytes, as UTF-16 has, gets the text for a file that
    is not UTF-8). An old Word file (.doc, an OLE file) gets a text of its
    own. A Word archive may have only stored and deflated members, as Word
    writes them, and no XML part over `DOCX_MAX_XML`.
"""

import io
import struct
import unicodedata
import zipfile
import zlib
from pathlib import PurePosixPath

from avtalsagent.domain.uploads import UploadKind
from avtalsagent.uploads.errors import (
    MISMATCH,
    NOT_UTF8,
    OLD_WORD,
    UNPACKED_TOO_LARGE,
    UNREADABLE,
    UNSUPPORTED,
    UploadRejected,
)

# The suffixes a name may have, and the kind each one means.
SUFFIXES = {".pdf": UploadKind.PDF, ".docx": UploadKind.DOCX, ".txt": UploadKind.TEXT}
SUFFIXES[".md"] = SUFFIXES[".markdown"] = UploadKind.TEXT

# What a Word file may unpack to, all members together, and how many members it may have.
# Agreements in Word are a few MB unpacked; images count too.
DOCX_MAX_UNPACKED = 100 * 1024 * 1024
DOCX_MAX_MEMBERS = 5000
# What one XML part may unpack to: python-docx holds it as a tree, many times its size. The
# document.xml of a contract of 300 pages is a few MB.
DOCX_MAX_XML = 16 * 1024 * 1024
_UNPACK_CHUNK = 1024 * 1024

_PDF = b"%PDF-"
_PDF_WINDOW = 1024
_ZIP = b"PK\x03\x04"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .doc, .xls and other old Office files
_TEXT_WINDOW = 8192
_WORD_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# A zip member's local header: its signature, then (after 22 bytes) its name's and extra
# field's lengths; the member's packed bytes follow the name and the extra field.
_LOCAL_HEADER = struct.Struct("<4s22xHH")
_KIND_NAMES = {UploadKind.PDF: "en PDF", UploadKind.DOCX: "en Word-fil", UploadKind.TEXT: "text"}

MAX_FILENAME_CHARS = 150
DEFAULT_FILENAME = "fil"


def detect_kind(data: bytes, filename: str, *, max_unpacked: int = DOCX_MAX_UNPACKED) -> UploadKind:
    """The file's kind from its content; `UploadRejected` when it is not read (see the module)."""
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix and suffix not in SUFFIXES:
        raise UploadRejected(415, OLD_WORD if suffix == ".doc" else UNSUPPORTED)
    kind: UploadKind | None = None
    if _PDF in data[:_PDF_WINDOW]:
        kind = UploadKind.PDF
    elif data.startswith(_ZIP):
        kind = UploadKind.DOCX if _is_word_archive(data, max_unpacked) else None
    elif data.startswith(_OLE):
        raise UploadRejected(415, OLD_WORD)
    elif suffix and b"\x00" not in data[:_TEXT_WINDOW]:
        kind = UploadKind.TEXT
    if kind is None and suffix and SUFFIXES[suffix] is UploadKind.TEXT:
        raise UploadRejected(422, NOT_UTF8)  # UTF-16, as Notepad's "Unicode", has NUL bytes
    if kind is None:
        raise UploadRejected(415, UNSUPPORTED)
    if suffix and SUFFIXES[suffix] is not kind:
        raise UploadRejected(415, MISMATCH.format(found=_KIND_NAMES[kind], suffix=suffix))
    return kind


def _is_word_archive(data: bytes, max_unpacked: int) -> bool:
    """Whether a zip archive is a Word document; 413 when it would unpack too large."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
    except (zipfile.BadZipFile, ValueError, EOFError):
        raise UploadRejected(422, UNREADABLE) from None
    if len(members) > DOCX_MAX_MEMBERS or sum(m.file_size for m in members) > max_unpacked:
        raise UploadRejected(413, UNPACKED_TOO_LARGE)
    if any(_is_xml(m.filename) and m.file_size > DOCX_MAX_XML for m in members):
        raise UploadRejected(413, UNPACKED_TOO_LARGE)
    # Word writes stored and deflated members, unencrypted; zipfile unpacks others unbounded.
    if any(m.compress_type not in _WORD_COMPRESSION or m.flag_bits & 0x1 for m in members):
        raise UploadRejected(422, UNREADABLE)
    try:
        honest = all(_unpacks_as_declared(data, member) for member in members)
    except zlib.error:
        honest = False
    if not honest:
        raise UploadRejected(422, UNREADABLE)
    return any(member.filename == "word/document.xml" for member in members)


def _unpacks_as_declared(data: bytes, member: zipfile.ZipInfo) -> bool:
    """Whether a member unpacks to the size its directory declares, a chunk at a time.

    It reads the same bytes as zipfile: from the end of the member's local
    header, as many as the directory says are packed. zipfile's own check
    of a member is its checksum, which the archive can set to match the
    declared part of a member that is larger.
    """
    start = member.header_offset
    header = data[start : start + _LOCAL_HEADER.size]
    if len(header) < _LOCAL_HEADER.size:
        return False
    signature, name_length, extra_length = _LOCAL_HEADER.unpack(header)
    if signature != _ZIP:
        return False
    begin = start + _LOCAL_HEADER.size + name_length + extra_length
    packed = memoryview(data)[begin : begin + member.compress_size]
    if member.compress_type == zipfile.ZIP_STORED:
        return len(packed) == member.file_size
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)  # a raw deflate stream, as zip has
    size = 0
    while packed:
        size += len(inflater.decompress(packed, _UNPACK_CHUNK))
        if size > member.file_size:
            return False
        packed = memoryview(inflater.unconsumed_tail)
    return size + len(inflater.flush()) == member.file_size


def _is_xml(name: str) -> bool:
    return name.lower().endswith((".xml", ".rels"))


def safe_filename(name: str) -> str:
    """The name's last part, without control or format characters, at most 150 characters.

    "../../avtal.pdf", and a Windows path to it, give "avtal.pdf"; leading
    and trailing dots and spaces go too, and a name left empty is "fil".
    """
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    # Cc: line breaks, NUL and other control characters; Cf: invisible format characters
    # such as the right-to-left override, which can make "fdp.exe" look like "exe.pdf".
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf"))
    name = " ".join(name.split()).strip(". ")
    path = PurePosixPath(name or DEFAULT_FILENAME)
    suffix = path.suffix if len(path.suffix) <= 10 else ""
    stem = path.name[: len(path.name) - len(suffix)] if suffix else path.name
    stem = stem[: MAX_FILENAME_CHARS - len(suffix)].strip(". ") or DEFAULT_FILENAME
    return stem + suffix
