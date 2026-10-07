"""Tests for avtalsagent.validation.citations: the citation check as a pure function.

Each test builds a draft as the model would hand it in and the sections a
reader would return, and checks the problems the model is told about and the
citations the user gets.
"""

import time
from typing import Any

import pytest
from pydantic import ValidationError

from avtalsagent.agent.schemas import MAX_QUOTE_LENGTH, DraftCitation, FinalAnswer
from avtalsagent.agent.sections import CitedSection
from avtalsagent.validation.citations import (
    MIN_QUOTE_LENGTH,
    CitationReport,
    check_citations,
    normalise,
)

SHA = "a1" * 32
OTHER_SHA = "b2" * 32
TEXT = (
    "Kontraktet kan sägas upp av Kunden med en uppsägningstid om tre (3) månader. "
    "Uppsägning ska ske skriftligen – per brev eller e-post."
)
SECTION = CitedSection(
    sha256=SHA,
    section_position=41,
    section_number="6.21.9",
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Större, fler än 200 anställda", "IT-drift Mindre"],
    page_start=14,
    text=TEXT,
)
SECTIONS: dict[tuple[str, int], CitedSection | None] = {(SHA, 41): SECTION}
QUOTE = "med en uppsägningstid om tre (3) månader"


def source(id: int = 1, quote: str = QUOTE, **fields: Any) -> dict[str, Any]:
    return {"id": id, "sha256": SHA, "section_position": 41, "quote": quote, **fields}


def check(
    text: str,
    *sources: dict[str, Any],
    sections: dict[tuple[str, int], CitedSection | None] = SECTIONS,
) -> CitationReport:
    draft = FinalAnswer(answered=True, text=text, citations=[DraftCitation(**s) for s in sources])
    return check_citations(draft, sections)


# --- normalise ----------------------------------------------------------------------


def test_normalise_unifies_what_typography_and_layout_change() -> None:
    assert normalise("  ”Tre (3)\n  MÅNADER” – „ok“ ’x’ ‑ — ") == (
        '"tre (3) månader" - "ok" \'x\' - -'
    )


def test_normalise_removes_soft_hyphens_and_the_line_break_after_one() -> None:
    assert normalise("uppsäg­nings­\n  tiden") == "uppsägningstiden"


def test_normalise_composes_letters_and_expands_compatibility_forms() -> None:
    assert normalise("månader ﬁl …") == "månader fil ..."


# --- quotes -------------------------------------------------------------------------


def test_a_verbatim_quote_passes() -> None:
    report = check("Tre månader [1].", source())

    assert report.problems == []
    assert [c.verified for c in report.citations] == [True]


@pytest.mark.parametrize(
    "quote",
    [
        "med en  uppsägningstid\nom tre (3) månader",  # whitespace
        "MED EN UPPSÄGNINGSTID OM TRE (3) MÅNADER",  # case
        "”med en uppsägningstid om tre (3) månader”",  # Swedish quote marks around it
        '"…med en uppsägningstid om tre (3) månader..."',  # quote marks and ellipses
        "upp­sägning ska ske skriftligen - per brev",  # soft hyphen, dash as hyphen
        "skriftligen — per brev eller e‑post.",  # em dash, non-breaking hyphen
    ],
)
def test_a_quote_that_differs_only_in_layout_passes(quote: str) -> None:
    assert check("Tre månader [1].", source(quote=quote)).problems == []


def test_a_quote_with_one_word_changed_fails() -> None:
    report = check("Tre månader [1].", source(quote="med en uppsägningstid på tre (3) månader"))

    assert report.problems == [
        "Källa [1]: citatet finns inte ordagrant i avsnitt 6.21.9 (Uppsägning). Kopiera det "
        "ur texten från read_section, utan utelämningar och utan egna ord."
    ]
    assert [c.verified for c in report.citations] == [False]


def test_a_quote_with_words_left_out_fails() -> None:
    report = check("Tre månader [1].", source(quote="Kontraktet kan sägas upp […] tre (3) månader"))

    assert len(report.problems) == 1
    assert report.problems[0].startswith("Källa [1]: citatet finns inte ordagrant")


def test_a_quote_shorter_than_the_minimum_fails() -> None:
    short = "tre (3) månader"[: MIN_QUOTE_LENGTH - 1]
    report = check("Tre månader [1].", source(quote=short))

    assert report.problems == [
        f"Källa [1]: citatet är för kort (minst {MIN_QUOTE_LENGTH} tecken). Citera en längre "
        "sammanhängande del av avsnitt 6.21.9 (Uppsägning)."
    ]
    assert [c.verified for c in report.citations] == [False]


def test_a_quote_of_only_quote_marks_is_too_short() -> None:
    report = check("Tre månader [1].", source(quote="”…”"))

    assert report.problems[0].startswith("Källa [1]: citatet är för kort")
    # Never an empty quote in the answer: the model's characters as written.
    assert report.citations[0].quote == "”…”"


def test_a_long_run_of_space_inside_a_quote_is_trimmed_and_checked_at_once() -> None:
    # Edge characters are looked for only at the ends, so the time grows with the quote's
    # length, not with its square (a search at every position took 40 s for 32,000 spaces).
    # model_construct: past the schema's length limit, as if it were not there.
    quote = "”Kunden med en" + "\n" * 100_000 + "uppsägningstid…”"
    draft = FinalAnswer.model_construct(
        answered=True,
        text="Tre månader [1].",
        citations=[DraftCitation.model_construct(**source(quote=quote))],
    )

    started = time.perf_counter()
    report = check_citations(draft, SECTIONS)
    elapsed = time.perf_counter() - started

    assert elapsed < 1
    assert report.problems == []
    assert report.citations[0].quote == quote[1:-2]  # without ” and …”


def test_a_quote_longer_than_the_limit_is_refused_as_malformed() -> None:
    with pytest.raises(ValidationError, match="quote"):
        DraftCitation(**source(quote="x" * (MAX_QUOTE_LENGTH + 1)))
    assert DraftCitation(**source(quote="x" * MAX_QUOTE_LENGTH)).quote == "x" * MAX_QUOTE_LENGTH


def test_a_short_quote_passes_when_it_is_the_whole_section() -> None:
    short = SECTION.model_copy(
        update={"text": "Utgår.", "section_number": None, "section_title": "Bilaga 4"}
    )
    report = check("Bilagan har utgått [1].", source(quote="utgår."), sections={(SHA, 41): short})

    assert report.problems == []


def test_a_quote_from_another_section_fails() -> None:
    other = SECTION.model_copy(update={"section_position": 7, "text": "Priset är fast."})
    sections = {**SECTIONS, (SHA, 7): other}

    report = check("Tre månader [1].", source(section_position=7), sections=sections)

    assert report.problems[0].startswith("Källa [1]: citatet finns inte ordagrant")


def test_a_section_without_a_number_is_named_by_its_title() -> None:
    untitled = SECTION.model_copy(update={"section_number": None, "section_title": "Inledning"})

    report = check(
        "Tre [1].", source(quote="tre (3) veckor och mer"), sections={(SHA, 41): untitled}
    )

    assert "finns inte ordagrant i avsnittet ”Inledning”." in report.problems[0]


# --- sections -----------------------------------------------------------------------


@pytest.mark.parametrize("sections", [{}, {(SHA, 41): None}], ids=["missing", "none"])
def test_a_section_the_reader_did_not_find_fails(
    sections: dict[tuple[str, int], CitedSection | None],
) -> None:
    report = check("Tre månader [1].", source(), sections=sections)

    assert report.problems == [
        f"Källa [1]: det finns inget avsnitt att läsa på plats 41 i dokumentet {SHA[:12]}…, "
        "eller så hålls det tillbaka. Kopiera sha256 och section_position från read_section."
    ]
    [citation] = report.citations
    assert citation.model_dump() == {
        "id": 1,
        "sha256": SHA,
        "file_title": "",
        "page_title": None,
        "section_number": None,
        "section_title": "",
        "page": None,
        "quote": QUOTE,
        "verified": False,
    }


def test_the_citation_is_copied_from_the_section_not_the_draft() -> None:
    report = check("Tre månader [1].", source(quote=f"  ”{QUOTE}…” "))

    [citation] = report.citations
    assert citation.model_dump() == {
        "id": 1,
        "sha256": SHA,
        "file_title": "Allmänna villkor",
        "page_title": "IT-drift Större, fler än 200 anställda",
        "section_number": "6.21.9",
        "section_title": "Uppsägning",
        "page": 14,
        "quote": QUOTE,  # without the marks around it, as the words to find on the page
        "verified": True,
    }


@pytest.mark.parametrize(
    ("chosen", "page_title"),
    [
        ("IT-drift Mindre", "IT-drift Mindre"),
        (None, "IT-drift Större, fler än 200 anställda"),
        ("Bemanningstjänster", "IT-drift Större, fler än 200 anställda"),
    ],
    ids=["one of the section's", "none chosen: the first", "not the section's: the first"],
)
def test_the_page_title_is_the_drafts_choice_among_the_sections(
    chosen: str | None, page_title: str
) -> None:
    [citation] = check("Tre månader [1].", source(page_title=chosen)).citations

    assert citation.page_title == page_title
    assert citation.verified is True  # the choice is not part of the check


def test_a_file_without_pages_or_page_titles_gives_none() -> None:
    word_file = SECTION.model_copy(update={"page_titles": [], "page_start": None})

    [citation] = check("Tre månader [1].", source(), sections={(SHA, 41): word_file}).citations

    assert (citation.page_title, citation.page) == (None, None)


def test_an_unnumbered_section_of_a_word_file_has_null_number_and_page() -> None:
    # The web app's contract allows null in these three fields.
    unnumbered = SECTION.model_copy(update={"section_number": None, "page_start": None})

    [citation] = check("Tre månader [1].", source(), sections={(SHA, 41): unnumbered}).citations

    assert citation.verified is True
    assert (citation.section_number, citation.page) == (None, None)
    assert citation.model_dump(mode="json")["section_number"] is None


def test_sources_are_checked_each_against_its_own_section() -> None:
    other = SECTION.model_copy(
        update={"sha256": OTHER_SHA, "file_title": "Avropsvägledning", "text": "Annan text."}
    )
    sections = {**SECTIONS, (OTHER_SHA, 41): other}

    report = check(
        "Tre månader [1], annat [2].", source(), source(2, sha256=OTHER_SHA), sections=sections
    )

    assert [(c.id, c.file_title, c.verified) for c in report.citations] == [
        (1, "Allmänna villkor", True),
        (2, "Avropsvägledning", False),
    ]
    assert len(report.problems) == 1
    assert report.problems[0].startswith("Källa [2]:")


# --- markers and ids ----------------------------------------------------------------


def test_several_sources_used_in_the_text_pass() -> None:
    second = source(2, quote="Uppsägning ska ske skriftligen")

    report = check("Tre månader [1]; skriftligen [1][2].", source(), second)

    assert report.problems == []
    assert [c.id for c in report.citations] == [1, 2]


def test_a_marker_without_a_source_fails() -> None:
    report = check("Tre månader [1], skriftligen [2].", source())

    assert report.problems == ["Hänvisningen [2] i texten har ingen källa med id 2."]
    assert [c.verified for c in report.citations] == [True]


def test_a_source_not_used_in_the_text_fails() -> None:
    report = check("Tre månader [1].", source(), source(2))

    assert report.problems == [
        "Källa [2] används inte i texten: sätt [2] efter påståendet den stöder, eller ta "
        "bort källan."
    ]


def test_a_list_of_numbers_in_one_marker_cites_each() -> None:
    # The prompt asks for [1][2]; [1, 2] is read the same way, as the web app reads it.
    second = source(2, quote="Uppsägning ska ske skriftligen")

    report = check("Tre månader, skriftligen [1, 2].", source(), second)

    assert report.problems == []


def test_a_number_in_a_list_marker_without_a_source_fails() -> None:
    report = check("Tre månader [1,3].", source())

    assert report.problems == ["Hänvisningen [3] i texten har ingen källa med id 3."]


def test_two_sources_with_one_id_fail() -> None:
    report = check("Tre månader [1].", source(), source())

    assert report.problems == ["Flera källor har id 1: ge varje källa ett eget id."]


def test_ids_with_a_gap_fail() -> None:
    report = check("Tre månader [1], skriftligen [3].", source(), source(3))

    assert report.problems == ["Källornas id ska vara 1–2 utan luckor, men är 1, 3."]


def test_a_text_with_markers_and_no_sources_fails() -> None:
    report = check("Tre månader [1].")

    assert report.problems == ["Hänvisningen [1] i texten har ingen källa med id 1."]
    assert report.citations == []


def test_a_text_without_markers_or_sources_passes() -> None:
    report = check("Avtalet gäller till 2027-03-31 enligt registret.")

    assert report == CitationReport(problems=[], citations=[])
