"""Read an uploaded file in a child process, with a time limit and a memory limit.

What:
    `ProcessParser`, which runs `parse_upload` (`parse.py`) on a file in a
    child process of its own: `await parser.parse(data, filename, limits)`
    gives the `ParsedFile`, or raises `UploadRejected` with the status and
    Swedish text the route answers with, also when the file took longer
    than `PARSE_SECONDS` to read (422) or the child died (422) or ran out
    of memory (413). `close()` stops it taking new files and kills the
    children still reading.

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
    On Linux, where the API's container runs, the children are started by
    multiprocessing's forkserver, which has imported this module (and so
    pypdfium2 and python-docx) once, so a child starts in milliseconds
    without the API's threads and connections. Elsewhere (macOS, where the
    command line can run outside the container, and Windows) they are
    spawned, the platform's own default: a fresh interpreter per file, some
    tenths of a second. A child does not run the program that started it
    when that is a package's `__main__` (`python -m avtalsagent.api`,
    `python -m avtalsagent.agent`); a script of its own needs
    multiprocessing's `if __name__ == "__main__":` guard.
    Each file runs in a thread of the parser's own pool, of
    `PARSE_WORKERS` threads, which starts the child, waits for its answer
    on a pipe for at most `PARSE_SECONDS`, and always kills and reaps it.
    The child sets its limits, reads the file and sends back the result or
    the refusal (`UploadRejected` as its status and text). Its limits: the
    address space (Linux), no core file (a hostile file that makes pdfium
    abort would otherwise write one of up to a gigabyte where core files
    are on), and CPU time a few seconds past the time limit, which a child
    the parent waits for never reaches but which ends one whose parent died
    without killing it. A child that ends without an answer (pdfium
    aborted, the kernel killed it) is logged with its exit code and the
    file is 422 as unreadable; a `MemoryError` is 413. What the child logs
    goes to its standard error.
"""

import asyncio
import logging
import math
import multiprocessing
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from avtalsagent.uploads.errors import TOO_HEAVY, TOO_SLOW, UNREADABLE, UploadRejected
from avtalsagent.uploads.parse import ParsedFile, UploadLimits, parse_upload

if sys.platform != "win32":
    import resource

_log = logging.getLogger(__name__)

# Seconds a file may take to read; a PDF of 300 pages takes a few.
PARSE_SECONDS = 60.0
# The address space a child may use. It starts at about 65 MB, with pypdfium2 and
# python-docx; a PDF of 300 pages, a PDF page of 1.4 million characters and a Word file with
# 16 MB of XML, the most it may have, took at most 450 MB when measured.
PARSE_MEMORY = 1024 * 1024 * 1024
# Files read at once; each child may take PARSE_MEMORY.
PARSE_WORKERS = 2
# CPU seconds a child may use past PARSE_SECONDS before the kernel kills it.
CPU_MARGIN = 5

Parse = Callable[[bytes, str, UploadLimits], ParsedFile]


def _context() -> Any:
    """Linux: the forkserver, with this module imported in it; elsewhere spawn."""
    if sys.platform != "linux" or "forkserver" not in multiprocessing.get_all_start_methods():
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
        self._lock = threading.Lock()
        self._reading: set[BaseProcess] = set()
        self._closed = False

    async def parse(self, data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
        """The file read in a child process; `UploadRejected` when it is not read."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._run, data, filename, limits)

    def close(self) -> None:
        """Take no new files, and kill the children still reading (their files are 422)."""
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._closed = True
            for child in self._reading:
                child.kill()

    def _run(self, data: bytes, filename: str, limits: UploadLimits) -> ParsedFile:
        receiver, sender = self._context.Pipe(duplex=False)
        child = self._context.Process(
            target=_child,
            args=(sender, self._parse, data, filename, limits, self._memory, self._seconds),
            name="upload-parse",
            daemon=True,
        )
        with receiver:
            try:
                child.start()
            finally:
                sender.close()  # the child's end: the pipe ends when the child does
            with self._lock:
                self._reading.add(child)
                if self._closed:  # closed while the child started
                    child.kill()
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
                with self._lock:
                    self._reading.discard(child)
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
    seconds: float,
) -> None:
    """In the child: the limits, the file read, and the result or refusal sent back."""
    if sys.platform != "win32":
        cpu = math.ceil(seconds) + CPU_MARGIN
        _lower(resource.RLIMIT_CPU, cpu)
        _lower(resource.RLIMIT_CORE, 0)
        if memory is not None and sys.platform == "linux":
            _lower(resource.RLIMIT_AS, memory)
    try:
        result: tuple[str, object] = ("parsed", parse(data, filename, limits))
    except UploadRejected as rejected:
        result = ("rejected", (rejected.status_code, rejected.detail))
    except MemoryError:
        result = ("rejected", (413, TOO_HEAVY))
    sender.send(result)
    sender.close()


def _lower(kind: int, limit: int) -> None:
    """Lower a limit, soft and hard alike, to `limit`; a limit already lower is kept."""
    for current in resource.getrlimit(kind):
        if current != resource.RLIM_INFINITY:
            limit = min(limit, current)
    resource.setrlimit(kind, (limit, limit))
