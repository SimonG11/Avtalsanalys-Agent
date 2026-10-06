"""Let a language model choose the section where the rules found only the file.

What:
    `match_titles` goes through the resolved references and, for two kinds that
    the rules leave without a section, asks a `TitleMatcher` which heading of
    the target file is meant:

    - a section title that matches no heading (status TITLE_MISSING, with the
      file that was searched as target): 14aa1cc8ee3d §9.17: "enligt avsnitt
      Försäljningsredovisning och administrativ avgift", where the heading is
      "9.15 Försäljningsredovisning och administrationsavgift";
    - a topic given instead of a section, after a document the rules resolved
      to one file (kind DOCUMENT, status RESOLVED, `topic` set): 0692da436391
      §1.10.2: "villkor i Allmänna villkor gällande viten, skadestånd och
      ersättningar".

    `candidates` gives the headings offered for a title, `outline` those for a
    topic. An answer that is one of them makes the reference RESOLVED to that
    section, with the rule "R4-llm" (title) or "R2-llm" (topic); any other
    answer leaves the reference as it was. `MatchStats` counts the questions.

Why:
    ADR 0009 decision 5: the model is used for these two cases only, and only
    to choose among the headings of a file the rules already found, so it can
    never point a reference at a file or section that is not there. Mapped
    before the rules were built, the pilot had 143 references whose title
    matches no heading (73 distinct) and 64 document references followed by a
    topic. difflib alone pairs 66 of the titles at a ratio of 0.85 or more, but
    with errors, so a model judges the candidates instead of a threshold. This
    module is pure: the matcher is a plain function, the OpenAI call and its
    cache are in `ingestion/llm_title_matcher.py`, and the tests pass fakes.

How:
    A title is compared with the phrase after the keyword (the mention's key,
    which runs to the end of the sentence): difflib's ratio between the start
    of the heading's title and the start of the phrase, word for word over the
    shorter of the two, after lower-casing and removing punctuation. The
    `MAX_CANDIDATES` best with a ratio of at least `MIN_SIMILARITY` are
    offered. A topic names subjects, not a heading ("viten, skadestånd och
    ersättningar" is covered by "2.19 Avtalsbrott och påföljder"), so letters
    say little; it is offered the two outline levels below what the rules
    resolved: the file's level-1 and level-2 headings, or the two levels under
    the section when R2 found the document as a chapter of the same file ("6
    Allmänna villkor" in a procurement document). A heading that two sections
    of the file share is never offered, since the answer would not name one
    section. The model also sees the sentence the reference is in.

    Only references with one target file are asked: a template linked from
    several pages can lead to a different file on each page. The status of a
    reference the model does not answer stays as the rules left it, so the rate
    without the model is the rate of the references with a rule other than
    "R4-llm" (an "R2-llm" reference was RESOLVED by R2 to the file already).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from avtalsagent.domain.extracted import (
    Reference,
    ReferenceKind,
    ReferenceStatus,
    ReferenceTarget,
)
from avtalsagent.domain.parsed import Section

# (phrase, the sentence it is in, candidate headings) -> the heading meant, or None.
TitleMatcher = Callable[[str, str, Sequence[str]], str | None]

TITLE_RULE = "R4-llm"
TOPIC_RULE = "R2-llm"

# In the pilot the heading the model chose was first or second in the list in 47
# of the 50 title questions it answered (fourth twice, fifth once); eight leave room
# to spare.
MAX_CANDIDATES = 8
# Low on purpose, since the model and not the ratio decides: right answers in the
# pilot start at 0.57 (54211e718d8e §9.7 "avsnitt Betalning av socialförsäkrings-
# avgifter" -> "9.4.2.2 Betalning av av socialförsäkringsavgifter", where the
# heading repeats a word). Below it a heading shares little more than a word.
MIN_SIMILARITY = 0.5
# The sentence around a reference is cut to this length; a table row can be long.
MAX_CONTEXT_CHARS = 400

_NOT_WORD = re.compile(r"[\W_]+")
# A sentence ends at a full stop, question or exclamation mark followed by space,
# or at a line break (blocks of a section are separated by blank lines).
_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n")


@dataclass(frozen=True)
class MatchStats:
    asked: int  # references the matcher was asked about
    answered: int  # ...that it answered with one of the candidates (now RESOLVED)
    # From a matcher that counts them (the OpenAI adapter): requests to the model,
    # and answers taken from its cache. 0 for a matcher that does not count.
    calls: int = 0
    cache_hits: int = 0


def similarity(phrase: str, title: str) -> float:
    """How closely the start of `phrase` matches the start of `title`, from 0 to 1.

    The two are compared word for word over the shorter of them. The phrase runs
    on after the title ("Försäljningsredovisning och administrativ avgift) de
    fyra (4) senaste kvartalen"), and a reference can give only the start of a
    title ("avsnitt Skäl för" for "3.2 Skäl för uteslutning"). Whole words keep
    "Ramavtal" from matching "Ramavtalets handlingar" as if it were equal.
    """
    title_words, phrase_words = _words(title), _words(phrase)
    shorter = min(len(title_words), len(phrase_words))
    if not shorter:
        return 0.0
    return SequenceMatcher(
        None, " ".join(title_words[:shorter]), " ".join(phrase_words[:shorter])
    ).ratio()


def candidates(phrase: str, sections: Sequence[Section]) -> list[str]:
    """The headings of a file most like a section title, best first."""
    scored = [
        (similarity(phrase, title), order, heading)
        for order, (heading, title) in enumerate(_unique_headings(sections))
    ]
    # Best first; equal ratios in document order.
    best = sorted(
        (item for item in scored if item[0] >= MIN_SIMILARITY), key=lambda i: (-i[0], i[1])
    )
    return [heading for _, _, heading in best[:MAX_CANDIDATES]]


def outline(sections: Sequence[Section], within: int | None = None) -> list[str]:
    """The headings of the two outline levels below `within`, in document order.

    `within` is the position of a section; None means the whole file, whose two
    levels are its level-1 and level-2 sections. A file whose only level-1
    section is the document itself, numbered as a chapter of the procurement
    it came from (622047b1ac6e: "10 Allmänna villkor", then 10.1-10.20), counts
    from that section, so its title is not offered as an answer.
    """
    chapters = [section.position for section in sections if section.level == 1]
    if within is None and len(chapters) == 1:
        within = chapters[0]
    depth: dict[int, int] = {}
    for section in sections:
        if within is None:
            depth[section.position] = section.level
        elif section.parent == within:
            depth[section.position] = 1
        elif section.parent is not None and section.parent in depth:
            depth[section.position] = depth[section.parent] + 1
    unique = {heading for heading, _ in _unique_headings(sections)}
    return [
        section.heading
        for section in sections
        if 1 <= depth.get(section.position, 0) <= 2 and section.heading in unique
    ]


def match_titles(
    references: Sequence[Reference],
    sections_by_sha: Mapping[str, Sequence[Section]],
    matcher: TitleMatcher,
) -> tuple[list[Reference], MatchStats]:
    """The references with the matcher's answers applied, and how many it was asked."""
    calls, cache_hits = _counter(matcher, "calls"), _counter(matcher, "cache_hits")
    asked = answered = 0
    result = []
    for reference in references:
        question = _question(reference, sections_by_sha)
        if question is None:
            result.append(reference)
            continue
        phrase, rule, positions = question
        asked += 1
        context = _context(reference, sections_by_sha)
        answer = matcher(phrase, context, list(positions))
        position = positions.get(answer) if answer is not None else None
        if position is None:
            result.append(reference)
            continue
        answered += 1
        targets = tuple(
            ReferenceTarget(sha256=target.sha256, section=position, page_url=target.page_url)
            for target in reference.targets
        )
        result.append(
            Reference(
                sha256=reference.sha256,
                mention=reference.mention,
                status=ReferenceStatus.RESOLVED,
                rule=rule,
                targets=targets,
            )
        )
    stats = MatchStats(
        asked=asked,
        answered=answered,
        calls=_counter(matcher, "calls") - calls,
        cache_hits=_counter(matcher, "cache_hits") - cache_hits,
    )
    return result, stats


def _question(
    reference: Reference, sections_by_sha: Mapping[str, Sequence[Section]]
) -> tuple[str, str, dict[str, int]] | None:
    """(phrase, rule, candidate heading -> section position), or None if not a case."""
    files = {target.sha256 for target in reference.targets}
    if len(files) != 1:
        return None  # no file known, or a template that leads to a different file per page
    sections = sections_by_sha.get(files.pop(), ())
    mention = reference.mention
    if reference.status is ReferenceStatus.TITLE_MISSING:
        phrase, rule = mention.key, TITLE_RULE
        headings = candidates(phrase, sections)
    elif (
        reference.status is ReferenceStatus.RESOLVED
        and mention.kind is ReferenceKind.DOCUMENT
        and mention.topic
    ):
        within = {target.section for target in reference.targets}
        if len(within) != 1:
            return None
        phrase, rule = mention.topic, TOPIC_RULE
        headings = outline(sections, within.pop())
    else:
        return None
    if not headings:
        return None
    position = {section.heading: section.position for section in sections if section.level}
    return phrase, rule, {heading: position[heading] for heading in headings}


def _context(reference: Reference, sections_by_sha: Mapping[str, Sequence[Section]]) -> str:
    """The sentence the mention is in, on one line; the mention itself if not found."""
    mention = reference.mention
    text = next(
        (
            section.text
            for section in sections_by_sha.get(reference.sha256, ())
            if section.position == mention.section
        ),
        None,
    )
    if text is None:
        return mention.raw
    begin = max((m.end() for m in _SENTENCE_END.finditer(text, 0, mention.start)), default=0)
    after = _SENTENCE_END.search(text, mention.end)
    end = after.end() if after else len(text)
    if end - begin > MAX_CONTEXT_CHARS:
        margin = max(0, (MAX_CONTEXT_CHARS - len(mention.raw)) // 2)
        begin, end = max(begin, mention.start - margin), min(end, mention.end + margin)
    return " ".join(text[begin:end].split())


def _unique_headings(sections: Sequence[Section]) -> list[tuple[str, str]]:
    """(heading, title) of the sections with a title of their own, if no other has it."""
    headed = [section for section in sections if section.level >= 1 and section.title]
    count: dict[str, int] = {}
    for section in headed:
        count[section.heading] = count.get(section.heading, 0) + 1
    return [(section.heading, section.title) for section in headed if count[section.heading] == 1]


def _words(text: str) -> list[str]:
    # A soft hyphen is inside a word ("Ramavtals\xadleverantören"), so it is removed
    # before other punctuation becomes a space.
    return _NOT_WORD.sub(" ", text.replace("\xad", "").lower()).split()


def _counter(matcher: TitleMatcher, name: str) -> int:
    """A count the matcher keeps as an attribute (the OpenAI adapter's calls), else 0."""
    value = getattr(matcher, name, 0)
    return value if isinstance(value, int) else 0
