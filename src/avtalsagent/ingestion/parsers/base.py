"""The interface every document parser implements.

What:
    `DocumentParser`: something with a `name` and a `parse(path)` method that
    returns the file's text as `Block`s in reading order. `ParseError`: raised
    when a file cannot be read.

Why:
    Step 2 and the tests depend on this interface only, so Docling can be
    replaced, or combined with an OCR service for scanned pages, by adding a
    class here without changing the steps around it.

How:
    A `typing.Protocol`, so a parser does not inherit from anything; any class
    with the same attributes fits. `name` is stored with each result, so a
    parser change can be seen in the data and old results can be parsed again.
    Whether a PDF page has a text layer is decided in step 2, not here, so the
    same check applies whichever parser reads the text.
"""

from pathlib import Path
from typing import Protocol

from avtalsagent.domain.parsed import Block


class ParseError(Exception):
    """Raised when a file cannot be parsed; the message says why."""


class DocumentParser(Protocol):
    # Parser and version, e.g. "docling 2.133.0 (layout heron onnx)".
    @property
    def name(self) -> str: ...

    def parse(self, path: Path) -> list[Block]:
        """Read the file at `path`; raise `ParseError` if it cannot be read."""
        ...
