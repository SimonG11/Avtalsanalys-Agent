"""Stand-ins for `parse_upload` that a child process of `ProcessParser` runs in the tests.

What:
    `abort` ends the process as pdfium does when it cannot allocate
    (SIGABRT), `sleep` takes longer than any time limit of the tests,
    `allocate` asks for more memory than the child may have, `refuse`
    raises `UploadRejected`, and `crash` raises another error.

Why:
    The child imports the function it runs by its name, so a stand-in
    must be a module's function, not a test's local one or a monkeypatch.

How:
    Each takes the arguments of `parse_upload`. The module imports little,
    so a child starts quickly.
"""

import os
import time

from avtalsagent.uploads.errors import UploadRejected
from avtalsagent.uploads.parse import ParsedFile, UploadLimits


def abort(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    os.abort()


def sleep(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    time.sleep(60)
    raise AssertionError("the child was not stopped")


def allocate(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    held = bytearray(4 * 1024**3)
    raise AssertionError(f"{len(held)} bytes were allocated past the memory limit")


def refuse(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    raise UploadRejected(415, f"{filename} har {len(data)} bytes")


def crash(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    raise KeyError("a reader's own error")
