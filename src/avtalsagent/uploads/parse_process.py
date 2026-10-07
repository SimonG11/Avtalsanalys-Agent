"""Read an uploaded file in a child process, with a time limit and a memory limit.

What:
    `ProcessParser`, which runs `parse_upload` (`parse.py`) on a file in a
    child process of its own: `await parser.parse(data, filename, limits)`
    gives the `ParsedFile`, or raises `UploadRejected` with the status and
    Swedish text the route answers with, also when the file took longer
    than `PARSE_SECONDS` to read (422) or the child died (422) or ran out
    of memory (413). `close()` stops it taking new files.

Why:
    Some of the work cannot be bounded before it is done: pdfium builds a
    page's text whole, about 160 bytes per character, and a PDF of 40 kB
    can hold one page of ten million characters, which takes gigabytes and
    seconds before the character limit can be checked. When pdfium cannot
    allocate, it aborts the process; no Python exception is raised. In the
    API's own process that would end every conversation in it, and a time
    limit on a worker thread only stops the waiting, not the thread, which
    would keep pdfium's lock and a thread of the default executor, where
    LangChain also runs the agent's callbacks. In a child process the work
    is stopped at the time limit (the child is killed), its memory is
    capped (RLIMIT_AS on Linux), and a child that dies takes nothing else
    with it. At most `PARSE_WORKERS` files are read at once, so a few
    uploads cannot take all the memory.

How:
    The children are started by multiprocessing's forkserver, which has
    imported this module (and so pypdfium2 and python-docx) once, so a
    child starts in milliseconds without the API's threads and
    connections; where there is no forkserver (Windows) they are spawned.
    A child does not run the program that started it when that is a
    package's `__main__` (`python -m avtalsagent.api`, `python -m
    avtalsagent.agent`); a script of its own needs multiprocessing's
    `if __name__ == "__main__":` guard.
    Each file runs in a thread of the parser's own pool, of
    `PARSE_WORKERS` threads, which starts the child, waits for its answer
    on a pipe for at most `PARSE_SECONDS`, and always kills and reaps it.
    The child sets its memory limit, reads the file and sends back the
    result or the refusal (`UploadRejected` as its status and text). A
    child that ends without an answer (pdfium aborted, the kernel killed
    it) is logged with its exit code and the file is 422 as unreadable; a
    `MemoryError` is 413. What the child logs goes to its standard error.
"""

import asyncio
import logging
import multiprocessing
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Connection
from typing import Any

from avtalsagent.uploads.errors import TOO_HEAVY, TOO_SLOW, UNREADABLE, UploadRejected
from avtalsagent.uploads.parse import ParsedFile, UploadLimits, parse_upload

if sys.platform == "linux":
    import resource

_log = logging.getLogger(__name__)

# Seconds a file may take to read; a PDF of 300 pages takes a few.
PARSE_SECONDS = 60.0
# The address space a child may use. It starts at about 65 MB, with pypdfium2 and
# python-docx; a PDF of 300 pages or a Word file with 16 MB of XML, the most it may have,
# took at most 320 MB when measured.
PARSE_MEMORY = 1024 * 1024 * 1024
# Files read at once; each child may take PARSE_MEMORY.
PARSE_WORKERS = 2

Parse = Callable[[bytes, str, UploadLimits], ParsedFile]


def _context() -> Any:
    """The forkserver of multiprocessing, with this module imported in it; spawn without one."""
    if "forkserver" not in multiprocessing.get_all_start_methods():
        return multiprocessing.get_context("spawn")
    context = multiprocessing.get_context("forkserver")
    context.set_forkserver_preload([__name__])  # no effect once the server runs
    return context


class ProcessParser:
    """Reads uploaded files in child processes (see the module)."""

    def __init__(
        self,
        *,
        seconds: float = PARSE_SECONDS,
        memory: int | None = PARSE_MEMORY,
        workers: int = PARSE_WORKERS,
        parse: Parse = parse_upload,
    ) -> None:
        self._seconds = seconds
        self._memory = memory
        self._parse = parse  # a module's function: the child imports it by name
        self._context = _context()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="upload-parse")

    async def parse(self, data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
        """The file read in a child process; `UploadRejected` when it is not read."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._run, data, filename, limits)

    def close(self) -> None:
        """Take no new files; a child already reading ends within the time limit."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
        receiver, sender = self._context.Pipe(duplex=False)
        child = self._context.Process(
            target=_child,
            args=(sender, self._parse, data, filename, limits, self._memory),
            name="upload-parse",
            daemon=True,
        )
        with receiver:
            try:
                child.start()
            finally:
                sender.close()  # the child's end: the pipe ends when the child does
            try:
                if not receiver.poll(self._seconds):
                    _log.warning("reading %r took more than %s s", filename, self._seconds)
                    raise UploadRejected(422, TOO_SLOW)
                outcome, value = receiver.recv()
            except EOFError:
                child.join(1)
                _log.warning(
                    "the process reading %r ended without an answer (exit code %s)",
                    filename,
                    child.exitcode,
                )
                raise UploadRejected(422, UNREADABLE) from None
            finally:
                child.kill()
                child.join()
        if outcome == "rejected":
            status_code, detail = value
            raise UploadRejected(status_code, detail)
        parsed: ParsedFile = value
        return parsed


def _child(
    sender: Connection,
    parse: Parse,
    data: bytes,
    filename: str,
    limits: UploadLimits,
    memory: int | None,
) -> None:
    """In the child: the memory limit, the file read, and the result or refusal sent back."""
    if memory is not None and sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    try:
        result: tuple[str, object] = ("parsed", parse(data, filename, limits))
    except UploadRejected as rejected:
        result = ("rejected", (rejected.status_code, rejected.detail))
    except MemoryError:
        result = ("rejected", (413, TOO_HEAVY))
    sender.send(result)
    sender.close()
