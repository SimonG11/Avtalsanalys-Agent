"""Tests for evals.gold: the gold file's form, and the sections its sources resolve to.

The section texts are made up in the style of the agreements' clauses. The
gold file of the repository is parsed too, so a line that breaks its form
fails here rather than in a measurement.
"""

import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from avtalsagent.domain.search import SearchFilters
from avtalsagent.ingestion.step6_index import section_hash
from evals.gold import (
    DocumentSource,
    GoldError,
    GoldQuestion,
    GoldScope,
    RegisterSource,
    SkippedQuestion,
    SkipReason,
    StoredSection,
    load_gold,
    parse_gold,
    resolve,
    retrieval_questions,
    search_filters,
    skip_reason,
)

GOLD_FILE = Path(__file__).parents[3] / "evals" / "datasets" / "gold_sv.jsonl"

TERMS = "a" * 64  # general terms
COPY = "b" * 64  # a procurement document that prints the general terms again
CARD = "c" * 64  # a supplier's card

PENALTY = (
    "6.4 Vite vid försening\n\nVite utgår med 5 000 kronor per påbörjad vecka som "
    "förseningen består."
)
# The same clause under another number: the same section text, so the same hash.
PENALTY_COPY = (
    "2.4 Vite vid försening\n\nVite utgår med 5 000 kronor per påbörjad vecka som\n"
    "förseningen består."
)
TERMINATION = "6.5 Uppsägning\n\nKunden får säga upp avtalet med tre månaders uppsägningstid."
PRICES = "Bilaga Priser\n\nTimpriset för en konsult på nivå 2 är högst 900 kronor."
DELIVERY = "3.1 Leverans\n\nLeverans sker inom fem arbetsdagar från beställning."
DELIVERY_ABROAD = "3.1 Leverans\n\nLeverans till ort utanför Sverige sker inom tio arbetsdagar."

SECTIONS = {
    TERMS: [
        StoredSection(0, None, "Allmänna villkor för exempelområdet"),
        StoredSection(1, "6.4", PENALTY),
        StoredSection(2, "6.5", TERMINATION),
    ],
    COPY: [StoredSection(7, "2.4", PENALTY_COPY)],
    # The card numbers two sections 3.1, as a document with a repeated heading does.
    CARD: [
        StoredSection(0, None, PRICES),
        StoredSection(1, "3.1", DELIVERY),
        StoredSection(2, "3.1", DELIVERY_ABROAD),
    ],
}


class FakeSections:
    """A `SectionLookup` on fixed sections that records which files it was asked for."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def sections(self, sha256: str) -> Sequence[StoredSection]:
        self.asked.append(sha256)
        return SECTIONS.get(sha256, [])


def alternative(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "sha256": TERMS,
        "file_title": "Allmänna villkor",
        "page_title": "Exempelområdet",
        "section_number": "6.4",
        "section_title": "Vite vid försening",
        "pages": [12],
        "quote": "Vite utgår med 5 000 kronor per påbörjad vecka",
    }
    return values | changes


def document(*alternatives: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "document", "alternatives": list(alternatives) or [alternative()]}


def question(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "t01",
        "category": "enkel uppslagning",
        "area": "Exempelområdet",
        "scope": {"framework_area": "Exempelområdet"},
        "question": "Hur stort är vitet om leveransen blir försenad?",
        "answer": "5 000 kronor per påbörjad vecka.",
        "answerable": True,
        "sources": [document()],
        "why_hard": "Frågan säger försenad, avtalet säger förseningen.",
        "difficulty": 1,
    }
    return values | changes


def gold_text(*questions: dict[str, Any]) -> str:
    return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in questions)


def parsed(**changes: Any) -> GoldQuestion:
    [result] = parse_gold(gold_text(question(**changes)))
    return result


# --- Parsing ----------------------------------------------------------------------------------


def test_a_question_becomes_frozen_values() -> None:
    result = parsed(
        sources=[
            document(
                alternative(), alternative(sha256=CARD, section_number=None, section_position=0)
            ),
            {
                "kind": "register",
                "agreement_numbers": ["23.3-0000-2024-001"],
                "fields": ["valid_to"],
            },
        ]
    )

    assert result.id == "t01"
    assert (result.category, result.area, result.difficulty) == (
        "enkel uppslagning",
        "Exempelområdet",
        1,
    )
    assert result.scope == GoldScope(framework_area="Exempelområdet")
    assert result.answerable
    first, second = result.document_sources[0].alternatives
    assert (first.sha256, first.section_number, first.section_position) == (TERMS, "6.4", None)
    assert first.pages == (12,)
    assert first.quote == "Vite utgår med 5 000 kronor per påbörjad vecka"
    assert (second.section_number, second.section_position) == (None, 0)
    assert result.sources[1] == RegisterSource(("23.3-0000-2024-001",), ("valid_to",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.id = "t02"  # type: ignore[misc]


def test_a_note_on_an_alternative_and_pages_of_null_are_accepted() -> None:
    result = parsed(
        sources=[document(alternative(note="Avsnittet saknar nummer i steg 3.", pages=None))]
    )

    assert result.document_sources[0].alternatives[0].pages is None


def test_a_question_without_an_answer_has_no_sources() -> None:
    result = parsed(answerable=False, sources=[], scope={})

    assert result.sources == ()
    assert result.scope == GoldScope()


def test_blank_lines_are_skipped() -> None:
    text = "\n" + gold_text(question()) + "   \n" + gold_text(question(id="t02"))

    assert [item.id for item in parse_gold(text)] == ["t01", "t02"]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("{not json", "line 1: not valid JSON"),
        ("[1, 2]", "line 1: expected a JSON object"),
        (question(id=""), "line 1: id is blank"),
        ({k: v for k, v in question().items() if k != "question"}, r"\(t01\): question is missing"),
        (question(question="   "), r"\(t01\): question is blank"),
        (question(answerable="ja"), "answerable must be true or false"),
        (question(difficulty=True), "difficulty must be a whole number of at least 0"),
        (question(scope={"area": "IT-drift"}), "scope: unknown key area"),
        (question(scope={"framework_area": 5}), "scope: framework_area must be a string"),
        (question(sources={"kind": "document"}), "sources must be a list"),
        (question(sources=[]), "an answerable question needs at least one source"),
        (question(sources=[{"kind": "webb"}]), "source 1: unknown kind 'webb'"),
        (
            question(sources=[{"kind": "document", "alternatives": []}]),
            "source 1: a document source needs at least one alternative",
        ),
        (
            question(sources=[{"kind": "document", "alternatives": [alternative()], "x": 1}]),
            "source 1: unknown key x",
        ),
        # A misspelt key would otherwise find the section by its number instead.
        (
            question(sources=[document(alternative(section_postion=3))]),
            "alternative 1: unknown key section_postion",
        ),
        (
            question(sources=[document(alternative(sha256="A" * 64))]),
            "sha256 must be 64 lowercase hex digits",
        ),
        (
            question(sources=[document(alternative(section_number=None))]),
            "a section without a number needs its section_position",
        ),
        (
            question(
                sources=[
                    document({k: v for k, v in alternative().items() if k != "section_number"})
                ]
            ),
            "alternative 1: section_number is missing",
        ),
        (
            question(sources=[document(alternative(section_position=-1))]),
            "section_position must be a whole number of at least 0",
        ),
        (
            question(sources=[document(alternative(pages=["12"]))]),
            "pages must be a list of page numbers or null",
        ),
        (question(sources=[document(alternative(quote=""))]), "alternative 1: quote is blank"),
        (
            question(sources=[{"kind": "register", "agreement_numbers": [1], "fields": []}]),
            "agreement_numbers must be a list of strings",
        ),
    ],
)
def test_a_line_of_the_wrong_form_is_a_gold_error(line: str | dict[str, Any], message: str) -> None:
    text = line if isinstance(line, str) else gold_text(line)

    with pytest.raises(GoldError, match=message):
        parse_gold(text)


def test_an_id_used_twice_is_a_gold_error() -> None:
    with pytest.raises(GoldError, match="line 2: the id t01 is used twice"):
        parse_gold(gold_text(question(), question()))


def test_a_file_without_questions_is_a_gold_error() -> None:
    with pytest.raises(GoldError, match="the gold file has no questions"):
        parse_gold("\n\n")


def test_load_gold_names_the_file_by_its_hash(tmp_path: Path) -> None:
    path = tmp_path / "guld.jsonl"
    path.write_text(gold_text(question(), question(id="t02")), encoding="utf-8")

    gold = load_gold(path)

    assert gold.path == path
    assert gold.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert [item.id for item in gold.questions] == ["t01", "t02"]


def test_a_missing_or_unreadable_gold_file_is_a_gold_error(tmp_path: Path) -> None:
    with pytest.raises(GoldError, match="cannot read the gold file .*saknas.jsonl"):
        load_gold(tmp_path / "saknas.jsonl")
    latin = tmp_path / "latin.jsonl"
    latin.write_bytes(gold_text(question()).encode("latin-1"))
    with pytest.raises(GoldError, match="is not UTF-8"):
        load_gold(latin)


def test_the_gold_file_of_the_repository_parses() -> None:
    gold = load_gold(GOLD_FILE)

    assert len(gold.questions) == 30
    assert len({item.id for item in gold.questions}) == 30
    assert Counter(skip_reason(item) for item in gold.questions) == {
        None: 22,
        SkipReason.REGISTER_ONLY: 4,
        SkipReason.NO_ANSWER: 4,
    }


# --- Whether the agent should ask the user ---------------------------------------------------

OPTIONS = ["IT-drift Större", "IT-drift Mindre"]


def test_a_question_without_the_ask_fields_reads_as_before() -> None:
    result = parsed()

    assert (result.should_ask, result.options, result.clarification) == (None, (), None)
    assert all(item.should_ask is None for item in load_gold(GOLD_FILE).questions)


def test_a_question_that_should_ask_has_its_options_and_clarification() -> None:
    result = parsed(should_ask=True, options=OPTIONS, clarification="Vi har IT-drift Mindre.")

    assert result.should_ask is True
    assert result.options == ("IT-drift Större", "IT-drift Mindre")
    assert result.clarification == "Vi har IT-drift Mindre."


def test_a_question_that_should_not_ask_may_name_the_options_or_none() -> None:
    named = parsed(should_ask=False, options=OPTIONS, clarification="Svara för båda.")
    bare = parsed(should_ask=False)

    assert (named.should_ask, named.options, named.clarification) == (
        False,
        tuple(OPTIONS),
        "Svara för båda.",
    )
    assert (bare.should_ask, bare.options, bare.clarification) == (False, (), None)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"should_ask": "ja"}, "should_ask must be true or false"),
        ({"should_ask": True, "clarification": "Mindre."}, "at least two options"),
        (
            {"should_ask": True, "options": ["Större"], "clarification": "Mindre."},
            "at least two options",
        ),
        ({"should_ask": True, "options": OPTIONS}, "should_ask needs a clarification"),
        (
            {"should_ask": True, "options": OPTIONS, "clarification": "  "},
            "clarification is blank",
        ),
        ({"should_ask": True, "options": "Större", "clarification": "x"}, "options must be a list"),
        (
            {"should_ask": True, "options": ["Större", 2], "clarification": "x"},
            "options must be a list of strings",
        ),
        (
            {"should_ask": True, "options": ["Större", " "], "clarification": "x"},
            "an option is blank",
        ),
        ({"options": OPTIONS}, "options and clarification need should_ask"),
        ({"clarification": "Mindre."}, "options and clarification need should_ask"),
    ],
)
def test_ask_fields_of_the_wrong_form_are_a_gold_error(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(GoldError, match=message):
        parse_gold(gold_text(question(**changes)))


# --- Which questions are measured, and how ----------------------------------------------------


def test_a_question_with_a_document_source_is_measured() -> None:
    register = {"kind": "register", "agreement_numbers": [], "fields": ["org_number"]}

    assert skip_reason(parsed()) is None
    assert skip_reason(parsed(sources=[register, document()])) is None
    assert skip_reason(parsed(sources=[register])) is SkipReason.REGISTER_ONLY
    assert skip_reason(parsed(answerable=False, sources=[])) is SkipReason.NO_ANSWER


def test_the_scope_gives_the_filters() -> None:
    scoped = parsed(scope={"framework_area": "IT-drift", "agreement_number": "23.3-0000-2024-002"})

    assert search_filters(scoped) == SearchFilters(
        framework_area="IT-drift", agreement_number="23.3-0000-2024-002"
    )
    assert search_filters(parsed(scope={})) == SearchFilters()


# --- Resolving the sources --------------------------------------------------------------------


def test_a_source_is_found_by_its_number_and_hashed_as_the_index_does() -> None:
    [required] = resolve(parsed(), FakeSections())

    assert required == {section_hash(PENALTY, "6.4")}


def test_copies_under_other_numbers_are_one_section() -> None:
    copy = alternative(sha256=COPY, section_number="2.4", quote="förseningen består")

    [required] = resolve(parsed(sources=[document(alternative(), copy)]), FakeSections())

    # The hash leaves out the number and the line breaks.
    assert required == {section_hash(PENALTY, "6.4")} == {section_hash(PENALTY_COPY, "2.4")}


def test_any_alternative_counts() -> None:
    prices = alternative(sha256=CARD, section_number=None, section_position=0, quote="900 kronor")

    [required] = resolve(parsed(sources=[document(alternative(), prices)]), FakeSections())

    assert required == {section_hash(PENALTY, "6.4"), section_hash(PRICES, None)}


def test_a_given_position_decides_between_sections_with_the_same_number() -> None:
    # "Leverans" is in both sections numbered 3.1; the position says which.
    first = alternative(sha256=CARD, section_number="3.1", section_position=2, quote="Leverans")

    [required] = resolve(parsed(sources=[document(first)]), FakeSections())

    assert required == {section_hash(DELIVERY_ABROAD, "3.1")}


def test_the_quote_decides_between_sections_with_the_same_number() -> None:
    abroad = alternative(sha256=CARD, section_number="3.1", quote="utanför Sverige")

    [required] = resolve(parsed(sources=[document(abroad)]), FakeSections())

    assert required == {section_hash(DELIVERY_ABROAD, "3.1")}


def test_each_document_source_is_one_required_source_in_order() -> None:
    termination = alternative(section_number="6.5", quote="tre månaders uppsägningstid")
    register = {"kind": "register", "agreement_numbers": [], "fields": ["valid_to"]}

    required = resolve(
        parsed(sources=[document(termination), register, document()]), FakeSections()
    )

    assert required == (
        frozenset({section_hash(TERMINATION, "6.5")}),
        frozenset({section_hash(PENALTY, "6.4")}),
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            alternative(sha256="d" * 64),
            "t01: section 6.4 of dddddddddddd: the file has no stored sections",
        ),
        (
            alternative(section_number="9.9"),
            "t01: section 9.9 of aaaaaaaaaaaa: the file has no section with that number",
        ),
        (
            alternative(sha256=CARD, section_number=None, section_position=5),
            "t01: the unnumbered section at position 5 of cccccccccccc: the file has no "
            "section at that position",
        ),
        # A moved position: the section there has another number.
        (
            alternative(section_number="6.5", section_position=1),
            r"t01: section 6.5 \(position 1\) of aaaaaaaaaaaa: the section at that position is "
            "numbered 6.4; the positions have moved",
        ),
        (
            alternative(section_position=0),
            r"t01: section 6.4 \(position 0\) of aaaaaaaaaaaa: the section at that position is "
            "unnumbered",
        ),
        (
            alternative(quote="Vite utgår med 10 000 kronor"),
            "t01: section 6.4 of aaaaaaaaaaaa: the quote is not in the section",
        ),
        # Verbatim: the stored text breaks the line after "vecka som".
        (
            alternative(
                sha256=COPY, section_number="2.4", quote="per påbörjad vecka som förseningen"
            ),
            "t01: section 2.4 of bbbbbbbbbbbb: the quote is not in the section",
        ),
        (
            alternative(sha256=CARD, section_number="3.1", quote="arbetsdagar"),
            r"t01: section 3.1 of cccccccccccc: the quote is in 2 sections with that number "
            r"\(positions 1, 2\); give section_position",
        ),
    ],
)
def test_a_source_that_does_not_match_the_sections_is_a_gold_error(
    source: dict[str, Any], message: str
) -> None:
    with pytest.raises(GoldError, match=message):
        resolve(parsed(sources=[document(source)]), FakeSections())


def test_two_sources_of_one_section_are_a_gold_error() -> None:
    copy = alternative(sha256=COPY, section_number="2.4", quote="förseningen består")

    with pytest.raises(
        GoldError,
        match=r"t01: document sources 1 and 2 are the same section \(section 2.4 of bbbbbbbbbbbb\)",
    ):
        resolve(parsed(sources=[document(), document(copy)]), FakeSections())


def test_retrieval_questions_resolves_the_measured_and_lists_the_skipped() -> None:
    questions = parse_gold(
        gold_text(
            question(id="t01", scope={"agreement_number": "23.3-0000-2024-002"}),
            question(
                id="t02",
                category="registerfråga",
                sources=[{"kind": "register", "agreement_numbers": [], "fields": ["org_number"]}],
            ),
            question(id="t03", category="fråga utan svar", answerable=False, sources=[]),
        )
    )
    lookup = FakeSections()

    measured, skipped = retrieval_questions(questions, lookup)

    [first] = measured
    assert first.question.id == "t01"
    assert first.required == (frozenset({section_hash(PENALTY, "6.4")}),)
    assert first.filters == SearchFilters(agreement_number="23.3-0000-2024-002")
    assert skipped == [
        SkippedQuestion("t02", "registerfråga", SkipReason.REGISTER_ONLY),
        SkippedQuestion("t03", "fråga utan svar", SkipReason.NO_ANSWER),
    ]
    assert lookup.asked == [TERMS]  # the skipped questions are not resolved


def test_document_sources_leave_out_the_register() -> None:
    register = RegisterSource(("23.3-0000-2024-001",), ("org_number",))
    source = parsed().document_sources[0]

    item = dataclasses.replace(parsed(), sources=(register, source))

    assert item.document_sources == (source,)
    assert isinstance(item.document_sources[0], DocumentSource)
