"""Document parsers: turn a PDF or Word file into a `ParsedDocument`.

What:
    `base.py` defines the `DocumentParser` interface. `docling_parser.py` is
    the implementation used today.

Why:
    The parser is the part most likely to be replaced (a newer Docling, an OCR
    service for scanned pages, M3b), so the rest of the ingestion only sees
    the interface and the `ParsedDocument` model (ADR 0008).

How:
    `ingestion/step2_parse.py` picks a parser and calls `parse` per file.
"""
