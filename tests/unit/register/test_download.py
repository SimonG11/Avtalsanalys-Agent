"""Tests for avtalsagent.register.download, with a fake HTTP server (no network)."""

from datetime import date
from pathlib import Path

import httpx
import pytest

from avtalsagent.register.download import DownloadError, download_register

URL = "https://www.avropa.se/giltigaramavtal/excel"


def client_returning(response: httpx.Response) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: response))


def test_xlsx_is_saved_with_todays_date(tmp_path: Path) -> None:
    content = b"PK\x03\x04 rest of the zip file"

    path = download_register(
        URL, tmp_path, client_returning(httpx.Response(200, content=content)), date(2026, 10, 5)
    )

    assert path == tmp_path / "giltiga-ramavtal-2026-10-05.xlsx"
    assert path.read_bytes() == content


def test_html_instead_of_xlsx_is_rejected(tmp_path: Path) -> None:
    client = client_returning(httpx.Response(200, content=b"<html>Not found</html>"))

    with pytest.raises(DownloadError):
        download_register(URL, tmp_path, client)
    assert list(tmp_path.iterdir()) == []


def test_http_error_is_raised(tmp_path: Path) -> None:
    client = client_returning(httpx.Response(503, request=httpx.Request("GET", URL)))

    with pytest.raises(httpx.HTTPStatusError):
        download_register(URL, tmp_path, client)
