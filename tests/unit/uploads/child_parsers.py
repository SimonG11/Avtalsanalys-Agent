"""Stand-ins for `parse_upload` that a child process of `ProcessParser` runs in the tests.

What:
    `abort` ends the process as pdfium does when it cannot allocate
    (SIGABRT), `sleep` takes longer than any time limit of the tests,
    `allocate` asks for more memory than the child may have,
    `reader_out_of_memory` is `parse_upload` with pdfium out of memory,
    `refuse` raises `UploadRejected`, `crash` raises another error, and
    `report_limits` refuses the file with the child's resource limits as
    its text.

Why:
    The child imports the function it runs by its name, so a stand-in
    must be a module's function, not a test's local one or a monkeypatch.

How:
    Each takes the arguments of `parse_upload`. The module imports little
    that the forkserver has not imported already, so a child starts
    quickly. `reader_out_of_memory` replaces pypdfium2's `PdfDocument` in
    the child it runs in, which ends with the file.
"""

import json
import os
import resource
import time

import pypdfium2 as pdfium

from avtalsagent.uploads.errors import UploadRejected
from avtalsagent.uploads.parse import ParsedFile, UploadLimits, parse_upload


def abort(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    os.abort()


def sleep(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    time.sleep(60)
    raise AssertionError("the child was not stopped")


def allocate(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    held = bytearray(4 * 1024**3)
    raise AssertionError(f"{len(held)} bytes were allocated past the memory limit")


def reader_out_of_memory(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    def exhausted(*args: object) -> object:
        raise MemoryError

    pdfium.PdfDocument = exhausted  # in this child only, which ends with the file
    return parse_upload(data, filename, limits)


def refuse(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    raise UploadRejected(415, f"{filename} har {len(data)} bytes")


def crash(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    raise KeyError("a reader's own error")


def report_limits(data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
    kinds = {"as": resource.RLIMIT_AS, "cpu": resource.RLIMIT_CPU, "core": resource.RLIMIT_CORE}
    raise UploadRejected(
        418, json.dumps({name: resource.getrlimit(k) for name, k in kinds.items()})
    )
