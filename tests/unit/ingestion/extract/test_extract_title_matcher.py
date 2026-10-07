"""Tests for avtalsagent.ingestion.extract.title_matcher.

The sentences and headings are real, from the M4 pilot files (avropa.se,
2026-10-05), cited as sha[:12] §section; long sentences are shortened at the
ends. The supplier "Ramavtal" 14aa1cc8ee3d numbers its main document as chapter
9; Allmänna villkor ab7277a9c123 is chapter 2 of its procurement; the procurement
document 124cb5f66cc3 holds Allmänna villkor as its chapter 6. Headings marked
"made up" are written for the test. No line names a person.
"""

from collections.abc import Sequence

from avtalsagent.domain.extracted import (
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
)
from avtalsagent.domain.parsed import Section
from avtalsagent.ingestion.extract.title_matcher import (
    MAX_CANDIDATES,
    MAX_CONTEXT_CHARS,
    MatchStats,
    candidates,
    match_titles,
    outline,
    similarity,
)

SOURCE = "14aa1cc8ee3d" + "0" * 52
TARGET = "ab7277a9c123" + "0" * 52
PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-konsulttjanster/"

# 14aa1cc8ee3d §9.18.2
VITE = (
    "Vite utgår med 0,25 % av Ramavtalsleverantörens rapporterade omsättning på Ramavtalet "
    "(enligt avsnitt Försäljningsredovisning och administrativ avgift) de fyra (4) senaste "
    "kvartalen för varje påbörjad vecka."
)
VITE_KEY = "Försäljningsredovisning och administrativ avgift) de fyra (4) senaste kvartalen"
# 0692da436391 §1.10.2
TOPIC = (
    "Ramavtalsleverantören har, vid avrop genom förnyad konkurrensutsättning, inte rätt att "
    "offerera högre timpris än de priser som anges i bilaga Priser. Detta gäller inte om "
    "Avropsberättigad i Avropet har anpassat krav gällande Konsultens kompetens eller villkor "
    "i Allmänna villkor gällande viten, skadestånd och ersättningar som är kostnadsdrivande "
    "för Ramavtalsleverantören."
)


def section(
    position: int,
    number: str | None,
    title: str,
    parent: int | None = None,
    *,
    text: str = "",
    level: int | None = None,
) -> Section:
    """A section; its level comes from the number, or is 1 for an unnumbered heading."""
    if level is None:
        level = len(number.split(".")) if number else 1
    return Section(
        position=position,
        number=number,
        title=title,
        level=level,
        parent=parent,
        path=(),
        page_start=None,
        page_end=None,
        text=text or f"{number or ''} {title}".strip(),
    )


# 14aa1cc8ee3d, chapter 9 "Ramavtalets Huvuddokument".
MAIN_DOCUMENT = (
    section(0, None, "Text före första rubriken", level=0),
    section(1, "9", "Ramavtalets Huvuddokument"),
    section(2, "9.1", "Ramavtal", 1),
    section(3, "9.2", "Avtalshandlingar", 1),
    section(4, "9.2.1", "Ramavtalets handlingar och deras inbördes ordning", 3),
    section(5, "9.9", "Ramavtalsleverantörens åtagande", 1),
    section(6, "9.9.1", "Försäkringar", 5),
    section(7, "9.15", "Försäljningsredovisning och administrationsavgift", 1),
    section(8, "9.17", "Uppföljning", 1),
    section(9, "9.18", "Avtalsbrott och påföljder", 1),
    section(10, "9.18.2", "Vite vid utebliven försäljningsredovisning", 9, text=VITE),
)

# ab7277a9c123, Allmänna villkor numbered as chapter 2 of its procurement (shortened).
GENERAL_TERMS = (
    section(0, None, "Text före första rubriken", level=0),
    section(1, "2", "Allmänna villkor"),
    section(2, "2.1", "Allmänt", 1),
    section(3, "2.18", "Uppföljning", 1),
    section(4, "2.19", "Avtalsbrott och påföljder", 1),
    section(5, "2.19.1", "Försening", 4),
    section(6, "2.19.1.3", "Vite vid Försening", 5),
    section(7, "2.19.8", "Skadestånd och ansvarsbegränsningar", 4),
)


def mention(
    raw: str,
    key: str,
    *,
    text: str,
    kind: ReferenceKind = ReferenceKind.SECTION_TITLE,
    topic: str | None = None,
    section_position: int = 10,
) -> ReferenceMention:
    start = text.index(raw)
    return ReferenceMention(
        section=section_position,
        start=start,
        end=start + len(raw),
        raw=raw,
        kind=kind,
        key=key,
        rule="R4" if kind is ReferenceKind.SECTION_TITLE else "R2",
        topic=topic,
    )


def title_missing(*targets: ReferenceTarget, text: str = VITE) -> Reference:
    raw = "avsnitt Försäljningsredovisning och administrativ avgift) de fyra (4) senaste kvartalen"
    return Reference(
        sha256=SOURCE,
        mention=mention(raw, VITE_KEY, text=text),
        status=ReferenceStatus.TITLE_MISSING,
        rule="R4",
        targets=targets,
    )


def topic_reference(*targets: ReferenceTarget, topic: str | None) -> Reference:
    return Reference(
        sha256=SOURCE,
        mention=mention(
            "Allmänna villkor",
            "allmänna villkor",
            text=TOPIC,
            kind=ReferenceKind.DOCUMENT,
            topic=topic,
            section_position=11,
        ),
        status=ReferenceStatus.RESOLVED,
        rule="R2",
        targets=targets,
    )


class Recorder:
    """A matcher that answers from a table and remembers what it was asked."""

    def __init__(self, answers: dict[str, str | None]) -> None:
        self.answers = answers
        self.questions: list[tuple[str, str, list[str]]] = []

    def __call__(self, phrase: str, context: str, headings: Sequence[str]) -> str | None:
        self.questions.append((phrase, context, list(headings)))
        return self.answers.get(phrase)


SECTIONS = {
    SOURCE: (*MAIN_DOCUMENT, section(11, "9.19", "Takpriser", 1, text=TOPIC)),
    TARGET: GENERAL_TERMS,
}


# similarity


def test_similarity_compares_the_start_of_the_phrase_word_for_word() -> None:
    # The key runs on after the title; only as many words as the title has count.
    assert similarity(VITE_KEY, "Försäljningsredovisning och administrationsavgift") > 0.85
    assert similarity(VITE_KEY, "Försäljningsredovisning och administrationsavgift") > similarity(
        VITE_KEY, "Uppföljning"
    )


def test_a_shorter_title_is_compared_by_whole_words() -> None:
    # 14aa1cc8ee3d §9.3.1 "uppräknade i avsnitt Ramavtalets handlingar": the heading
    # "9.1 Ramavtal" is the first eight letters, but not the first word.
    assert similarity("Ramavtalets handlingar", "Ramavtal") < 0.9
    assert similarity("Ramavtalets handlingar", "Ramavtalets handlingar och deras ordning") == 1.0


def test_a_phrase_that_gives_only_the_start_of_the_title_matches_it() -> None:
    # 6b6040e2dc2c §1.12: "uteslutningsgrund enligt avsnitt Skäl för" (the line breaks there).
    assert similarity("Skäl för", "Skäl för uteslutning") == 1.0


def test_case_and_punctuation_do_not_count() -> None:
    # 18309f4961d3 §3.2.1: "(se vidare under punkt Tilldelningskriterium Leveranskvalitet)".
    assert (
        similarity(
            "Tilldelningskriterium Leveranskvalitet)", "Tilldelningskriterium - Leveranskvalitet"
        )
        == 1.0
    )
    assert (
        similarity("Ramavtals\xadleverantörens åtagande", "Ramavtalsleverantörens åtagande") == 1.0
    )
    assert similarity("AVTALSBROTT OCH PÅFÖLJDER", "Avtalsbrott och påföljder") == 1.0  # made up


# candidates


def test_candidates_are_the_most_similar_headings_best_first() -> None:
    found = candidates(VITE_KEY, MAIN_DOCUMENT)

    assert found[0] == "9.15 Försäljningsredovisning och administrationsavgift"
    assert "9.17 Uppföljning" not in found  # below MIN_SIMILARITY: shares no word


def test_candidates_are_at_most_max_candidates() -> None:
    many = [
        section(i, f"4.{i}", f"Kvalitet i utförande av Konsulttjänst {i}") for i in range(1, 12)
    ]

    found = candidates("Kvalitet i utförande av angiven Konsulttjänst", many)

    assert len(found) == MAX_CANDIDATES
    assert found[0] == "4.1 Kvalitet i utförande av Konsulttjänst 1"  # equal ratios: document order


def test_the_text_before_the_first_heading_is_no_candidate() -> None:
    assert candidates("Text före första rubriken", MAIN_DOCUMENT) == []


def test_a_heading_two_sections_share_is_no_candidate() -> None:
    # 0ba5ca07759e, Microsoft's Product Terms: "Begränsningar" heads four sections.
    sections = (
        section(1, None, "Betalning och avgifter"),
        section(2, None, "Begränsningar"),
        section(3, None, "Begränsningar"),
    )

    assert "Begränsningar" not in candidates("Begränsningar", sections)
    assert candidates("Betalningar och avgifter", sections) == ["Betalning och avgifter"]


# outline


def test_outline_of_a_file_numbered_as_one_chapter_starts_below_the_chapter() -> None:
    # "2 Allmänna villkor" is the whole file and would say nothing; 2.19.1 is a third level.
    assert outline(GENERAL_TERMS) == [
        "2.1 Allmänt",
        "2.18 Uppföljning",
        "2.19 Avtalsbrott och påföljder",
        "2.19.1 Försening",
        "2.19.8 Skadestånd och ansvarsbegränsningar",
    ]


def test_outline_of_a_file_with_several_chapters_is_its_first_two_levels() -> None:
    sections = (
        section(0, "1", "Inbjudan"),
        section(1, "1.1", "Om upphandlingen", 0),
        section(2, "1.1.1", "Bakgrund", 1),  # made up
        section(3, "2", "Administrativa förutsättningar och krav"),
    )

    assert outline(sections) == [
        "1 Inbjudan",
        "1.1 Om upphandlingen",
        "2 Administrativa förutsättningar och krav",
    ]


def test_outline_within_a_section_is_the_two_levels_under_it() -> None:
    # 124cb5f66cc3: R2 found "Allmänna villkor" as the file's own chapter 6.
    sections = (
        section(0, "5", "Ramavtalets Huvuddokument"),
        section(1, "5.15", "Avtalsbrott och påföljder", 0),
        section(2, "6", "Allmänna villkor"),
        section(3, "6.19", "Avtalsbrott och påföljder", 2),
        section(4, "6.19.2", "Fel", 3),
        section(5, "6.19.2.2", "Avhjälpande av Fel vid utförande av Konsulttjänst", 4),
    )

    assert outline(sections, within=2) == ["6.19 Avtalsbrott och påföljder", "6.19.2 Fel"]


# match_titles


def test_a_title_the_matcher_recognises_resolves_to_its_section() -> None:
    reference = title_missing(ReferenceTarget(sha256=SOURCE, section=None, page_url=PAGE))
    matcher = Recorder({VITE_KEY: "9.15 Försäljningsredovisning och administrationsavgift"})

    [resolved], stats = match_titles([reference], SECTIONS, matcher)

    assert resolved.status is ReferenceStatus.RESOLVED
    assert resolved.rule == "R4-llm"
    assert resolved.targets == (ReferenceTarget(sha256=SOURCE, section=7, page_url=PAGE),)
    assert resolved.mention == reference.mention
    assert stats == MatchStats(asked=1, answered=1)


def test_the_matcher_gets_the_sentence_of_the_reference() -> None:
    text = "Ramavtalet gäller i fyra år. " + VITE + "\n\nVitet faktureras."  # made up around it
    sections = {SOURCE: (*MAIN_DOCUMENT[:10], section(10, "9.18.2", "Vite", 9, text=text))}
    reference = title_missing(
        ReferenceTarget(sha256=SOURCE, section=None, page_url=None), text=text
    )
    matcher = Recorder({})

    match_titles([reference], sections, matcher)

    [(phrase, context, headings)] = matcher.questions
    assert phrase == VITE_KEY
    assert context == VITE
    assert headings[0] == "9.15 Försäljningsredovisning och administrationsavgift"


def test_an_answer_that_is_no_candidate_is_ignored() -> None:
    reference = title_missing(ReferenceTarget(sha256=SOURCE, section=None, page_url=None))
    # A heading of the file, but not offered: it shares no word with the reference.
    matcher = Recorder({VITE_KEY: "9.17 Uppföljning"})

    [unchanged], stats = match_titles([reference], SECTIONS, matcher)

    assert "9.17 Uppföljning" not in matcher.questions[0][2]
    assert unchanged == reference
    assert stats == MatchStats(asked=1, answered=0)


def test_a_long_sentence_is_cut_around_the_reference() -> None:
    # A table row has no full stop: 14aa1cc8ee3d §9.3.1 "Ramavtal | Med Ramavtal avses ...".
    text = "Begrepp | " + "Med Part avses Kammarkollegiet och Ramavtalsleverantören, " * 10 + VITE
    sections = {SOURCE: (*MAIN_DOCUMENT[:10], section(10, "9.3.1", "Begrepp", 9, text=text))}
    reference = title_missing(
        ReferenceTarget(sha256=SOURCE, section=None, page_url=None), text=text
    )
    matcher = Recorder({})

    match_titles([reference], sections, matcher)

    context = matcher.questions[0][1]
    assert len(context) <= MAX_CONTEXT_CHARS
    assert reference.mention.raw in context


def test_without_the_source_section_the_matcher_gets_the_mention_itself() -> None:
    reference = title_missing(ReferenceTarget(sha256=SOURCE, section=None, page_url=None))
    sections = {SOURCE: MAIN_DOCUMENT[:10]}  # §9.18.2 is not there
    matcher = Recorder({})

    match_titles([reference], sections, matcher)

    assert matcher.questions[0][1] == reference.mention.raw


def test_no_answer_leaves_the_reference_as_the_rules_left_it() -> None:
    reference = title_missing(ReferenceTarget(sha256=SOURCE, section=None, page_url=None))

    [unchanged], stats = match_titles([reference], SECTIONS, Recorder({}))

    assert unchanged == reference
    assert stats.answered == 0


def test_a_title_without_a_target_file_or_with_several_is_not_asked() -> None:
    other = "f249bae4f967" + "0" * 52
    references = [
        title_missing(),
        title_missing(
            ReferenceTarget(sha256=SOURCE, section=None, page_url=PAGE),
            ReferenceTarget(sha256=other, section=None, page_url=PAGE + "x"),
        ),
    ]
    matcher = Recorder({VITE_KEY: "9.15 Försäljningsredovisning och administrationsavgift"})

    result, stats = match_titles(references, SECTIONS, matcher)

    assert result == references
    assert stats.asked == 0


def test_a_title_with_no_candidate_is_not_asked() -> None:
    reference = title_missing(ReferenceTarget(sha256=TARGET, section=None, page_url=None))
    sections = {TARGET: (section(1, "1", "Inbjudan"), section(2, "2", "Kravspecifikation"))}

    result, stats = match_titles([reference], sections, Recorder({}))

    assert result == [reference]
    assert stats.asked == 0


def test_a_topic_after_a_resolved_document_resolves_to_a_section_of_its_outline() -> None:
    reference = topic_reference(
        ReferenceTarget(sha256=TARGET, section=None, page_url=None),
        topic="viten, skadestånd och ersättningar",
    )
    matcher = Recorder({"viten, skadestånd och ersättningar": "2.19 Avtalsbrott och påföljder"})

    [resolved], stats = match_titles([reference], SECTIONS, matcher)

    assert matcher.questions[0][2] == outline(GENERAL_TERMS)
    assert resolved.rule == "R2-llm"
    assert resolved.targets == (ReferenceTarget(sha256=TARGET, section=4, page_url=None),)
    assert stats == MatchStats(asked=1, answered=1)


def test_a_topic_in_a_chapter_of_the_same_file_is_offered_that_chapter_only() -> None:
    reference = topic_reference(
        ReferenceTarget(sha256=TARGET, section=4, page_url=None),
        topic="viten, skadestånd och ersättningar",
    )
    matcher = Recorder({})

    match_titles([reference], SECTIONS, matcher)

    assert matcher.questions[0][2] == [
        "2.19.1 Försening",
        "2.19.1.3 Vite vid Försening",
        "2.19.8 Skadestånd och ansvarsbegränsningar",
    ]


def test_a_document_without_a_topic_is_not_asked() -> None:
    reference = topic_reference(
        ReferenceTarget(sha256=TARGET, section=None, page_url=None), topic=None
    )

    result, stats = match_titles([reference], SECTIONS, Recorder({}))

    assert result == [reference]
    assert stats.asked == 0


def test_stats_take_calls_and_cache_hits_from_a_matcher_that_counts_them() -> None:
    class Counting(Recorder):
        calls = 3  # from an earlier run
        cache_hits = 0

        def __call__(self, phrase: str, context: str, headings: Sequence[str]) -> str | None:
            self.calls += 1
            return super().__call__(phrase, context, headings)

    reference = title_missing(ReferenceTarget(sha256=SOURCE, section=None, page_url=None))

    _, stats = match_titles([reference, reference], SECTIONS, Counting({}))

    assert stats == MatchStats(asked=2, answered=0, calls=2, cache_hits=0)
