"""Register layer: the Excel master list "Alla giltiga ramavtal" into Postgres.

What:
    Downloads the list, reads it, normalises every row and loads the result
    into the register tables.

Why:
    The register is the ground truth: it says which agreements exist, with
    which suppliers, sub-areas and dates. The agent answers date and supplier
    questions from it, and ingestion checks every PDF against it (M4).

How:
    One step per file, run in this order:
    `download.py` -> `read_excel.py` -> `normalize.py` -> `load.py`.
    `__main__.py` runs the whole chain from the command line.
"""
