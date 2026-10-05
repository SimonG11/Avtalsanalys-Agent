"""Download the Excel master list from avropa.se.

What:
    `download_register` fetches "Alla giltiga ramavtal" as an xlsx file and
    saves it under the data directory.

Why:
    The master list changes when agreements are added or expire. Downloading
    it directly means the register can be refreshed with one command instead
    of a manual upload, and the file on disk shows exactly what was loaded.

How:
    One HTTP GET with httpx (redirects followed, errors raised). The file is
    written to a temporary name first and then renamed, so a failed download
    never leaves half a file behind. The caller can pass its own
    `httpx.Client`, which lets the tests run without network access.
"""

from datetime import date
from pathlib import Path

import httpx

# Every xlsx file is a zip archive, and every zip archive starts with these bytes.
_ZIP_MAGIC = b"PK\x03\x04"


class DownloadError(RuntimeError):
    """Raised when the response is not an Excel file."""


def download_register(
    url: str,
    target_dir: Path,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> Path:
    """Download the master list to `target_dir/giltiga-ramavtal-<date>.xlsx`."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"giltiga-ramavtal-{(today or date.today()).isoformat()}.xlsx"

    own_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=60.0)
    try:
        response = http.get(url)
        response.raise_for_status()
    finally:
        if own_client:
            http.close()

    if not response.content.startswith(_ZIP_MAGIC):
        raise DownloadError(f"{url} did not return an xlsx file")

    partial = target.with_suffix(".part")
    partial.write_bytes(response.content)
    partial.replace(target)
    return target
