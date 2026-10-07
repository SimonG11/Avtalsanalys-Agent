"""Tests for avtalsagent.uploads.file_type: what a file is, from its content, and a safe name.

Each kind is recognised from its bytes; a name that disagrees, another type,
an old Word file, a zip that is not Word and a Word file that unpacks too
large (a zip bomb) are refused with their status, also when its directory
lies about a member's size and checksum, packs it with bzip2 or marks it
encrypted, or when one XML part is too large; a text file in UTF-16 gets the
text for a file that is not UTF-8; names lose path parts and control
characters.
"""

import io
import struct
import zipfile
import zlib

import pytest

from avtalsagent.domain.uploads import UploadKind
from avtalsagent.uploads.errors import (
    NOT_UTF8,
    OLD_WORD,
    UNPACKED_TOO_LARGE,
    UNREADABLE,
    UNSUPPORTED,
    UploadRejected,
)
from avtalsagent.uploads.file_type import DOCX_MAX_XML, detect_kind, safe_filename
from tests.unit.uploads.upload_files import AGREEMENT_PAGES, docx_bytes, pdf_bytes, zip_bytes


def rejected(data: bytes, filename: str, **options: int) -> UploadRejected:
    with pytest.raises(UploadRejected) as caught:
        detect_kind(data, filename, **options)
    return caught.value


def test_each_kind_is_read_from_the_content() -> None:
    word = docx_bytes(lambda document: document.add_paragraph("Hej"))

    assert detect_kind(pdf_bytes(AGREEMENT_PAGES), "avtal.pdf") is UploadKind.PDF
    assert detect_kind(pdf_bytes(AGREEMENT_PAGES), "utan-namn") is UploadKind.PDF
    assert detect_kind(word, "Avtal.DOCX") is UploadKind.DOCX
    assert detect_kind("Hej på dig".encode(), "noter.txt") is UploadKind.TEXT
    assert detect_kind(b"# Rubrik", "noter.md") is UploadKind.TEXT


def test_a_name_that_disagrees_with_the_content_is_415() -> None:
    error = rejected(b"MZ\x90\x00 an executable", "avtal.pdf")
    assert (error.status_code, error.detail) == (415, UNSUPPORTED)

    error = rejected(pdf_bytes(AGREEMENT_PAGES), "avtal.docx")
    assert error.status_code == 415
    assert "en PDF" in error.detail and ".docx" in error.detail


@pytest.mark.parametrize("filename", ["tabell.xlsx", "bild.png", "skript.exe", "sida.html"])
def test_other_types_are_415(filename: str) -> None:
    error = rejected(b"whatever", filename)

    assert (error.status_code, error.detail) == (415, UNSUPPORTED)


def test_an_old_word_file_gets_a_text_of_its_own() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100

    assert rejected(ole, "avtal.doc").detail == OLD_WORD
    assert rejected(ole, "avtal.txt").detail == OLD_WORD  # the content decides


def test_a_zip_that_is_not_word_and_text_without_a_name_are_415() -> None:
    sheet = zip_bytes({"xl/workbook.xml": b"<workbook/>"})

    assert rejected(sheet, "avtal.docx").status_code == 415
    assert rejected(b"text without a name", "").status_code == 415


@pytest.mark.parametrize("data", ["Avtal\n1. Parter".encode("utf-16"), b"text\x00with a nul"])
def test_a_text_file_with_nul_bytes_gets_the_text_for_utf8(data: bytes) -> None:
    # UTF-16, as Notepad saves "Unicode", has a NUL byte in every ASCII character.
    error = rejected(data, "avtal.txt")

    assert (error.status_code, error.detail) == (422, NOT_UTF8)


def test_a_broken_zip_is_422() -> None:
    error = rejected(b"PK\x03\x04 and then nothing", "avtal.docx")

    assert (error.status_code, error.detail) == (422, UNREADABLE)


def test_a_word_file_that_unpacks_too_large_is_413_before_it_is_opened() -> None:
    # 10 MB of zeros compress to about 10 kB: a small zip bomb against a 1 MB limit.
    bomb = zip_bytes({"word/document.xml": b"<w/>", "word/media/zeros.bin": bytes(10_000_000)})
    assert len(bomb) < 100_000

    error = rejected(bomb, "avtal.docx", max_unpacked=1_000_000)

    assert (error.status_code, error.detail) == (413, UNPACKED_TOO_LARGE)
    assert detect_kind(bomb, "avtal.docx") is UploadKind.DOCX  # under the default 100 MB


def test_a_word_file_with_too_many_members_is_413() -> None:
    members = {"word/document.xml": b"<w/>"} | {f"x/{i}": b"" for i in range(5001)}

    assert rejected(zip_bytes(members), "avtal.docx").status_code == 413


def declared(archive: bytes, name: bytes, size: int, crc: int | None = None) -> bytes:
    """The archive with its directory saying that member `name` unpacks to `size` bytes."""
    patched = bytearray(archive)
    at = 0
    while (at := patched.find(b"PK\x01\x02", at)) >= 0:
        length = struct.unpack_from("<H", patched, at + 28)[0]
        if patched[at + 46 : at + 46 + length] == name:
            struct.pack_into("<I", patched, at + 24, size)
            if crc is not None:
                struct.pack_into("<I", patched, at + 16, crc)
        at += 4
    return bytes(patched)


@pytest.mark.parametrize("forged_crc", [False, True])
def test_a_member_larger_than_its_directory_says_is_refused_before_it_is_read(
    forged_crc: bool,
) -> None:
    # zipfile would unpack it in one call of up to 1 GiB before cutting it to the declared
    # size; with the checksum of the declared part, zipfile's own check passes too.
    start = b"<w:document/>"
    archive = zip_bytes({"word/document.xml": start + bytes(20_000_000)})
    lying = declared(
        archive, b"word/document.xml", len(start), zlib.crc32(start) if forged_crc else None
    )
    assert len(lying) < 100_000
    assert sum(m.file_size for m in zipfile.ZipFile(io.BytesIO(lying)).infolist()) < 1000

    error = rejected(lying, "avtal.docx")

    assert (error.status_code, error.detail) == (422, UNREADABLE)


def test_a_member_packed_with_bzip2_or_encrypted_is_422() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr("word/document.xml", b"<w/>")
    encrypted = bytearray(zip_bytes({"word/document.xml": b"<w/>"}))
    at = encrypted.find(b"PK\x01\x02")
    struct.pack_into("<H", encrypted, at + 8, struct.unpack_from("<H", encrypted, at + 8)[0] | 1)

    for data in (buffer.getvalue(), bytes(encrypted)):
        error = rejected(data, "avtal.docx")
        assert (error.status_code, error.detail) == (422, UNREADABLE)


def test_an_xml_part_over_its_limit_is_413() -> None:
    large = zip_bytes({"word/document.xml": b" " * (DOCX_MAX_XML + 1)})
    image = zip_bytes({"word/document.xml": b"<w/>", "word/media/a.png": bytes(DOCX_MAX_XML + 1)})

    assert rejected(large, "avtal.docx").detail == UNPACKED_TOO_LARGE
    assert detect_kind(image, "avtal.docx") is UploadKind.DOCX  # the limit is the XML's


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("avtal.pdf", "avtal.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\simon\\Avtal 2026.docx", "Avtal 2026.docx"),
        ("rad\r\nbrytning.txt", "radbrytning.txt"),
        ("fdp\u202e.exe", "fdp.exe"),  # the right-to-left override is gone
        ("  .dold.pdf ", "dold.pdf"),
        ("", "fil"),
        ("../", "fil"),
        ("Sjöräddning – villkor.pdf", "Sjöräddning – villkor.pdf"),
    ],
)
def test_a_name_loses_path_parts_and_control_characters(name: str, safe: str) -> None:
    assert safe_filename(name) == safe


def test_a_long_name_is_cut_but_keeps_its_suffix() -> None:
    name = safe_filename("a" * 400 + ".pdf")

    assert len(name) == 150
    assert name.endswith("a.pdf")
