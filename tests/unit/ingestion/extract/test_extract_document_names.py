"""Tests for avtalsagent.ingestion.extract.document_names.

The titles are link titles on avropa.se (2026-10-05); the soft hyphen is put in
by hand, since no pilot title has one.
"""

from avtalsagent.ingestion.extract.document_names import canonical_name, normalise_title


def test_soft_hyphens_are_removed_from_a_title() -> None:
    title = "Personuppgifts\u00adbiträdes\u00adavtal"

    assert normalise_title(title) == "personuppgiftsbiträdesavtal"
    assert canonical_name(normalise_title(title)) == "personuppgiftsbiträdesavtal"


def test_draft_prefix_and_typos_in_link_titles() -> None:
    assert normalise_title("Utkast till Säkerhetssyddsavtal (Nivå 1)") == (
        "säkerhetsskyddsavtal (nivå 1)"
    )
