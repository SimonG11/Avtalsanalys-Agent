"""Document parsers: turn a PDF or Word file into `Block`s in reading order.

What:
    `base.py` defines the `DocumentParser` interface. `docling_parser.py` is
    the implementation used today.

Why:
    The parser is the part most likely to be replaced (a newer Docling, an OCR
    service for scanned pages, M3b), so the rest of the ingestion only sees
    the interface and the `Block` model (ADR 0008).

How:
    `ingestion/step2_parse.py` picks a parser, calls `parse` per file and
    builds the `ParsedDocument` from the blocks and its own page check.
"""
