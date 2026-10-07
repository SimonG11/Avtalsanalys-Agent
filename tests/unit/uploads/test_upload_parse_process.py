"""Tests for avtalsagent.uploads.parse_process: an uploaded file read in a child process.

A file is read as `parse_upload` reads it, and a refusal comes back with its
status and text; a child that takes too long is killed (422), one that
aborts as pdfium does, or fails in another way, is 422 and logged with its
exit code, and one that passes its memory limit is 413, while the parent
goes on. A PDF with one page of a million characters, 40 kB on disk, is
refused without taking the parent's memory. The children are not left
running.
"""

import multiprocessing
import resource
import sys
import time

import pytest

from avtalsagent.uploads.errors import TOO_HEAVY, TOO_SLOW, UNREADABLE, UploadRejected
from avtalsagent.uploads.parse import UploadLimits, parse_upload
from avtalsagent.uploads.parse_process import Parse, ProcessParser
from tests.unit.uploads import child_parsers
from tests.unit.uploads.upload_files import AGREEMENT_PAGES, pdf_bytes, text_bomb_pdf

LIMITS = UploadLimits(max_bytes=10 * 1024 * 1024, max_pages=300, max_characters=1_500_000)
PDF = pdf_bytes(AGREEMENT_PAGES)

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is set on Linux")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def refusal(parser: ProcessParser, data: bytes = PDF) -> tuple[int, str]:
    try:
        with pytest.raises(UploadRejected) as raised:
            await parser.parse(data, "avtal.pdf", LIMITS)
    finally:
        parser.close()
    return raised.value.status_code, raised.value.detail


def no_children_left() -> bool:
    return not [child for child in multiprocessing.active_children() if child.is_alive()]


@pytest.mark.anyio
async def test_a_file_is_read_as_parse_upload_reads_it() -> None:
    parser = ProcessParser()
    try:
        parsed = await parser.parse(PDF, "avtal.pdf", LIMITS)
    finally:
        parser.close()

    assert parsed == parse_upload(PDF, "avtal.pdf", LIMITS)
    assert no_children_left()


@pytest.mark.anyio
async def test_a_refusal_comes_back_with_its_status_and_text() -> None:
    parser = ProcessParser(parse=child_parsers.refuse)

    assert await refusal(parser) == (415, f"avtal.pdf har {len(PDF)} bytes")


@pytest.mark.anyio
async def test_a_child_that_takes_too_long_is_killed_and_the_file_is_422(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = ProcessParser(seconds=0.5, parse=child_parsers.sleep)
    started = time.monotonic()

    assert await refusal(parser) == (422, TOO_SLOW)
    assert time.monotonic() - started < 10
    assert no_children_left()
    assert "took more than 0.5 s" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("parse", "exit_code"), [(child_parsers.abort, "-6"), (child_parsers.crash, "1")]
)
async def test_a_child_that_dies_is_422_and_the_parent_goes_on(
    parse: Parse, exit_code: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert await refusal(ProcessParser(parse=parse)) == (422, UNREADABLE)
    assert f"ended without an answer (exit code {exit_code})" in caplog.text

    parser = ProcessParser()  # the next file is read as usual
    try:
        assert (await parser.parse(PDF, "avtal.pdf", LIMITS)).pages == 4
    finally:
        parser.close()


@linux_only
@pytest.mark.anyio
async def test_a_child_past_its_memory_limit_is_413() -> None:
    parser = ProcessParser(parse=child_parsers.allocate)

    assert await refusal(parser) == (413, TOO_HEAVY)


@linux_only
@pytest.mark.anyio
async def test_one_page_of_a_million_characters_is_refused_in_the_child() -> None:
    data = text_bomb_pdf(pages=1, lines=10_000)  # 1 000 000 characters on one page
    parser = ProcessParser(memory=128 * 1024 * 1024)  # far below what pdfium needs for it
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    status, _ = await refusal(parser, data)

    assert len(data) < 40_000
    assert status in (413, 422)  # MemoryError in Python, or pdfium's abort
    grown_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    assert grown_kb < 100 * 1024
