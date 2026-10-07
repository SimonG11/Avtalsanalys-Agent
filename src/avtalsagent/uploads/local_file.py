"""A file on this machine as an upload: for the command line's `--fil`.

What:
    `read_local_file(path, thread_id, limits)` reads a file from disk and
    gives the `NewUpload` the store takes, read into sections by the same
    rules and limits as an upload through the API (`parse.py`). A file that
    is refused raises `UploadRejected` with the API's Swedish text; one that
    cannot be opened raises `OSError`.

Why:
    The command line is the demo's fallback when the web app is not there
    (ADR 0022), and the quickest way to try a comparison by hand: `python
    -m avtalsagent.agent --fil avtal.pdf "Jämför ..."` must treat the file
    exactly as the web app's upload would, so what is tried in the terminal
    holds in the web app.

How:
    The size is checked before the file is read, so a large file is
    refused without loading it. The name is the file's own, sanitized as
    the API sanitizes an uploaded name (`file_type.safe_filename`).
"""

import hashlib
from pathlib import Path

from avtalsagent.domain.uploads import NewUpload
from avtalsagent.uploads.errors import TOO_LARGE, UploadRejected
from avtalsagent.uploads.file_type import safe_filename
from avtalsagent.uploads.parse import UploadLimits, megabytes, parse_upload


def read_local_file(path: Path, thread_id: str, limits: UploadLimits) -> NewUpload:
    """The file at `path` as an upload of the thread, or `UploadRejected` (see the module)."""
    if path.stat().st_size > limits.max_bytes:
        raise UploadRejected(413, TOO_LARGE.format(limit=megabytes(limits.max_bytes)))
    data = path.read_bytes()
    filename = safe_filename(path.name)
    parsed = parse_upload(data, filename, limits)
    return NewUpload(
        thread_id=thread_id,
        filename=filename,
        kind=parsed.kind,
        sha256=hashlib.sha256(data).hexdigest(),
        content=data,
        pages=parsed.pages,
        characters=parsed.characters,
        warnings=parsed.warnings,
        sections=parsed.sections,
    )
