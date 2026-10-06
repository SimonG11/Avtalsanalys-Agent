"""Domain layer: plain data types and pure rules, with no database, network or LLM.

What:
    The concepts the whole system talks about (agreements, suppliers,
    sub-areas, agreement pages, documents and their sections) and the rules
    for normalising their identifiers.

Why:
    Keeping these free of dependencies means they can be tested and explained
    in isolation, and every other layer uses the same definitions.

How:
    `identifiers.py` normalises raw identifiers; `register.py` defines one
    normalised row of the master list; `documents.py` defines the agreement
    pages on avropa.se and their document links; `parsed.py` defines a
    document's content after parsing (blocks, sections, chunks). Other layers
    import from here, never the other way round.
"""
