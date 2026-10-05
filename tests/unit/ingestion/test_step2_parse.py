"""Tests for avtalsagent.ingestion.step2_parse, with PDFs made by reportlab."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.parsers.base import ParseError
from avtalsagent.ingestion.step2_parse import (
    ParseStatus,
    SourceFile,
    inspect_pages,
    load_cached,
    parse_files,
)

BODY = (
    "Ramavtalsleverantören ska under hela avtalsperioden ha en ansvarsförsäkring "
    "som täcker skador som kan uppstå i samband med uppdraget."
)


def make_pdf(target: Path, tmp_path: Path) -> Path:
    """Three pages: text, a scanned page (an image, no text layer), a short cover."""
    canvas = Canvas(str(target), pagesize=A4)
    width, height = A4
    canvas.drawString(72, height - 72, "6.6.6 Försäkring")
    canvas.drawString(72, height - 100, BODY[:90])
    canvas.drawString(72, height - 114, BODY[90:])
    canvas.showPage()
    scan = Image.new("RGB", (600, 850), "white")
    ImageDraw.Draw(scan).text((50, 50), "Underskrift", fill="black")
    scan_path = tmp_path / "scan.png"
    scan.save(scan_path)
    canvas.drawImage(str(scan_path), 0, 0, width, height)
    canvas.showPage()
    canvas.drawString(72, height - 72, "Bilaga 2")
    canvas.showPage()
    canvas.save()
    return target


def test_inspect_pages_flags_only_the_scanned_page(tmp_path: Path) -> None:
    pages = inspect_pages(make_pdf(tmp_path / "doc.pdf", tmp_path))
    assert [(p.number, p.needs_ocr) for p in pages] == [(1, False), (2, True), (3, False)]
    assert pages[0].char_count > 100
    assert pages[1].char_count == 0
    assert pages[2].char_count == len("Bilaga2")  # whitespace is not counted


class FakeParser:
    def __init__(self, name: str = "fake 1", fail: bool = False) -> None:
        self._name = name
        self.fail = fail
        self.calls: list[Path] = []

    @property
    def name(self) -> str:
        return self._name

    def parse(self, path: Path) -> list[Block]:
        self.calls.append(path)
        if self.fail:
            raise ParseError("broken file")
        return [Block(kind=BlockKind.HEADING, text="6.6.6 Försäkring", page=1)]


@pytest.fixture
def pdf_file(tmp_path: Path) -> SourceFile:
    return SourceFile("aa" * 32, "pdf", make_pdf(tmp_path / "doc.pdf", tmp_path))


def test_a_result_is_stored_and_reused(tmp_path: Path, pdf_file: SourceFile) -> None:
    parser = FakeParser()
    cache = tmp_path / "parsed"
    [first] = parse_files([pdf_file], parser, cache)
    [second] = parse_files([pdf_file], parser, cache)
    assert (first.status, second.status) == (ParseStatus.PARSED, ParseStatus.CACHED)
    assert len(parser.calls) == 1
    assert second.document == first.document
    stored = load_cached(cache, pdf_file.sha256)
    assert stored is not None
    assert stored.pages_needing_ocr == [2]
    assert stored.parser == "fake 1"


def test_a_new_parser_version_parses_again(tmp_path: Path, pdf_file: SourceFile) -> None:
    cache = tmp_path / "parsed"
    list(parse_files([pdf_file], FakeParser("fake 1"), cache))
    newer = FakeParser("fake 2")
    [result] = parse_files([pdf_file], newer, cache)
    assert result.status is ParseStatus.PARSED
    assert len(newer.calls) == 1


def test_a_failure_is_reported_and_not_stored(tmp_path: Path, pdf_file: SourceFile) -> None:
    cache = tmp_path / "parsed"
    [result] = parse_files([pdf_file], FakeParser(fail=True), cache)
    assert result.status is ParseStatus.FAILED
    assert result.message == "broken file"
    assert load_cached(cache, pdf_file.sha256) is None


def test_a_word_file_has_no_pages(tmp_path: Path) -> None:
    word = SourceFile("bb" * 32, "docx", tmp_path / "doc.docx")
    [result] = parse_files([word], FakeParser(), tmp_path / "parsed")
    assert result.document is not None
    assert result.document.pages == ()


def test_an_unreadable_cache_file_is_parsed_again(tmp_path: Path, pdf_file: SourceFile) -> None:
    cache = tmp_path / "parsed"
    cache.mkdir()
    (cache / f"{pdf_file.sha256}.json").write_text('{"old": "format"}')
    [result] = parse_files([pdf_file], FakeParser(), cache)
    assert result.status is ParseStatus.PARSED
