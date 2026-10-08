"""Tests for avtalsagent.uploads.local_file: a file on disk read as the API reads an upload."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from avtalsagent.domain.uploads import UploadKind
from avtalsagent.uploads.errors import TOO_LARGE, UploadRejected
from avtalsagent.uploads.local_file import read_local_file
from avtalsagent.uploads.parse import UploadLimits
from avtalsagent.uploads.parse_process import ProcessParser
from tests.unit.agent.uploaded import CONTRACT, LIMITS, new_upload


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def parser() -> Iterator[ProcessParser]:
    parser = ProcessParser(workers=1)
    yield parser
    parser.close()


@pytest.mark.anyio
async def test_a_file_is_the_upload_the_api_would_store(
    tmp_path: Path, parser: ProcessParser
) -> None:
    path = tmp_path / "avtal.md"
    path.write_bytes(CONTRACT)

    upload = await read_local_file(path, "cli-1", LIMITS, parser)

    assert upload == new_upload("cli-1", "avtal.md", CONTRACT)
    assert upload.kind is UploadKind.TEXT


@pytest.mark.anyio
async def test_a_file_too_large_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parser: ProcessParser
) -> None:
    path = tmp_path / "stor.md"
    path.write_bytes(CONTRACT)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("the file was read"))
    limits = UploadLimits(max_bytes=10, max_pages=300, max_characters=1_000)

    with pytest.raises(UploadRejected) as raised:
        await read_local_file(path, "cli-1", limits, parser)

    assert raised.value.status_code == 413
    assert raised.value.detail == TOO_LARGE.format(limit="0 kB")


@pytest.mark.anyio
async def test_the_name_is_sanitized_as_an_uploaded_name(
    tmp_path: Path, parser: ProcessParser
) -> None:
    path = tmp_path / "avtal‮fdp.md"
    path.write_bytes(CONTRACT)

    assert (await read_local_file(path, "cli-1", LIMITS, parser)).filename == "avtalfdp.md"
