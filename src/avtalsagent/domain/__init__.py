"""Domain layer: plain data types and pure rules, with no database, network or LLM.

What:
    The concepts the whole system talks about (agreements, suppliers, sub-areas)
    and the rules for normalising their identifiers.

Why:
    Keeping these free of dependencies means they can be tested and explained
    in isolation, and every other layer uses the same definitions.

How:
    `identifiers.py` normalises raw identifiers; `register.py` defines one
    normalised row of the master list. Other layers import from here, never
    the other way round.
"""
