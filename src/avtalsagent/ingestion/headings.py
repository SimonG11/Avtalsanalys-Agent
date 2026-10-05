"""Find the numbered section headings of a document.

What:
    `find_candidates` picks the blocks that start like a numbered heading
    ("14.2 Leverantörens uppsägning", "6. Allmänna villkor").
    `select_outline` chooses the candidates that together form a consistent
    table of contents and returns them as `Heading`s with number and level.

Why:
    Agreements are cited by section number, so the numbers must be right. A
    line starting with a number is not always a heading: numbered list items
    ("2. Leverantören ska ..."), table rows, amounts and wrapped lines also
    start with digits, and a layout model sometimes calls a heading plain
    text. One thing separates real headings from the rest: their numbers
    follow each other in outline order (6.6.8 is followed by 6.6.9, 6.7 or
    7, never by 2). Choosing the candidates that best fit that order removes
    most false headings without rules for each kind of document.

How:
    Each candidate gets a score: higher when the parser marked it as a heading
    and when it is short like a title, lower for list items and for lines
    that end with a page number (table-of-contents entries). A dynamic
    programme then finds the chain of candidates with the highest total
    score in which each number may follow the previous one: the first child
    (6.6 -> 6.6.1), the next number at the same or a higher level
    (6.6.8 -> 6.6.9, 6.7, 7) or, with a penalty, a small gap or a restart at 1
    (a new appendix). Pure functions on `Block`s, so every rule has a test.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from avtalsagent.domain.parsed import Block, BlockKind

# A section number at the start of a block: "6", "6.", "6.21.9", "6.2.", followed by
# a space or tab and the title. Components have at most three digits, so years and
# dates ("2024-09-25") are never numbers.
_NUMBERED = re.compile(
    r"^(?P<number>\d{1,3}(?:\.\d{1,3}){0,5})\.?[ \t\u00a0]+(?P<title>\S.*)$", re.S
)
# A block that is only a number; the title is in the next block.
_NUMBER_ONLY = re.compile(r"^(?P<number>\d{1,3}(?:\.\d{1,3}){0,5})\.?$")
# A table-of-contents entry ends with a page number: after a tab or dot leaders
# ("1\tParter\t3", "Allmänt ....... 4"), or after a plain space ("6.6.1 Dokumentation 8"),
# which a heading such as "Bilaga 12 och Bilaga 13" can also do, so a plain space only
# counts for a numbered line whose neighbouring blocks are entries too.
_TOC_LEADER = re.compile(r"(?:\t|\.{3,}|…)\s*\d{1,3}$")
_TRAILING_NUMBER = re.compile(r"\s\d{1,3}$")
_TOC_WINDOW = 2  # blocks on each side
_TOC_MAX_CHARS = 200
_OPENING_QUOTES = "\"'”“„«(["
# A list number stuck to the start of a title: "7.9 1.Åtaganden vid nyttjanderättstidens
# slut". It is removed from the title, so the heading is still found.
_FUSED_LIST_NUMBER = re.compile(r"^\d{1,2}\.(?=[A-ZÅÄÖ])")

# Scores and penalties of the outline search. A chain is a list of candidates;
# its value is the sum of their scores minus the penalties of each step.
_HEADING_LABEL_BONUS = 1.0  # the parser marked the block as a heading
_SHORT_TITLE_BONUS = 0.5  # short and without a full stop, like a title
_LIST_ITEM_PENALTY = 0.5
_IN_TOC_BONUS = 1.0  # the number is listed in the document's own table of contents
_GAP_PENALTY = 0.75  # per missing number, e.g. 6.2 -> 6.4...
_MAX_GAP_PENALTY_STEPS = 3  # ...counted up to three missing numbers
_SKIPPED_LEVEL_PENALTY = 1.0  # per level without a heading, e.g. 6 -> 6.1.1
_MAX_SKIPPED_LEVELS = 2
_DEEP_START_PENALTY = 1.0  # per level below the top for the first heading, e.g. 2.5.1
_MAX_LOOKBACK = 400  # candidates; keeps the search fast on very long documents

SHORT_TITLE_CHARS = 100
_MIN_TITLE_LETTERS = 2
_MAX_NUMBER_PART = 200  # larger parts are amounts or ids, not section numbers
_CANDIDATE_KINDS = (BlockKind.HEADING, BlockKind.TEXT, BlockKind.LIST_ITEM, BlockKind.TITLE)


@dataclass(frozen=True)
class Candidate:
    """A block (or two, when the number is alone) that starts like a numbered heading."""

    index: int  # position of the block in the list given to find_candidates
    consumed: int  # number of blocks it covers: 1, or 2 when the number was alone
    number: tuple[int, ...]
    title: str
    score: float


@dataclass(frozen=True)
class Heading:
    """A block that starts a section."""

    index: int
    consumed: int
    number: str | None  # "6.21.9"; None for a heading without a number
    level: int  # 3 for "6.21.9"
    title: str


def parse_number(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def toc_entries(blocks: Sequence[Block]) -> set[int]:
    """Indices of the blocks that are table-of-contents entries."""
    strong: set[int] = set()
    weak: set[int] = set()
    for index, block in enumerate(blocks):
        if block.kind is BlockKind.TOC:
            strong.add(index)
        elif block.kind is not BlockKind.TABLE and len(block.text) <= _TOC_MAX_CHARS:
            text = block.text.strip()
            if _TOC_LEADER.search(text):
                strong.add(index)
            elif _NUMBERED.match(text) and _TRAILING_NUMBER.search(text):
                weak.add(index)
    marked = strong | weak
    entries = set(strong)
    for index in weak:
        window = range(index - _TOC_WINDOW, index + _TOC_WINDOW + 1)
        if sum(1 for other in window if other != index and other in marked) >= 2:
            entries.add(index)
    return entries


def find_candidates(blocks: Sequence[Block]) -> list[Candidate]:
    """Every block outside the table of contents that starts like a numbered heading."""
    toc = toc_entries(blocks)
    # Numbers listed in the document's own table of contents are very likely headings.
    in_toc = {
        parse_number(match["number"])
        for index in toc
        for line in blocks[index].text.splitlines()
        if (match := _NUMBERED.match(line.strip()))
    }
    candidates: list[Candidate] = []
    for index, block in enumerate(blocks):
        if index in toc or block.kind not in _CANDIDATE_KINDS:
            continue
        text = block.text.strip()
        consumed = 1
        match = _NUMBERED.match(text)
        if match is None:
            alone = _NUMBER_ONLY.match(text)
            following = blocks[index + 1] if index + 1 < len(blocks) else None
            if alone is None or following is None or following.kind not in _CANDIDATE_KINDS:
                continue
            number_text, title = alone["number"], following.text.strip()
            consumed = 2
        else:
            number_text, title = match["number"], match["title"]
        title = _FUSED_LIST_NUMBER.sub("", " ".join(title.split("\n", 1)[0].split()))
        if not _looks_like_title(title):
            continue
        number = parse_number(number_text)
        # Sections are numbered from 1; "17.00" and "1.0" are times and versions.
        if any(part > _MAX_NUMBER_PART or part == 0 for part in number):
            continue
        score = _score(block, title) + (_IN_TOC_BONUS if number in in_toc else 0.0)
        candidates.append(Candidate(index, consumed, number, title, score))
    return candidates


def _looks_like_title(title: str) -> bool:
    """A title starts with a capital letter, possibly after a quote or bracket.

    It has at least two letters ("X" is a ticked box) and, unlike a list item,
    does not end with a comma or semicolon.
    """
    stripped = title.lstrip(_OPENING_QUOTES)
    return (
        sum(1 for char in stripped if char.isalpha()) >= _MIN_TITLE_LETTERS
        and stripped[0].isalpha()
        and stripped[0].isupper()
        and not title.endswith((",", ";"))
    )


def _score(block: Block, title: str) -> float:
    score = 1.0
    if block.kind is BlockKind.HEADING:
        score += _HEADING_LABEL_BONUS
    if block.kind is BlockKind.LIST_ITEM:
        score -= _LIST_ITEM_PENALTY
    if len(title) <= SHORT_TITLE_CHARS and not title.endswith("."):
        score += _SHORT_TITLE_BONUS
    return score


def step_penalty(previous: tuple[int, ...], current: tuple[int, ...]) -> float | None:
    """The penalty for `current` following `previous` in an outline; None if it cannot.

    `current` must move on at some level: the first child (6.6 -> 6.6.1) or the
    next number at the same or a higher level (6.6.8 -> 6.6.9, 6.7, 7). Missing
    numbers in between (6.2 -> 6.4) and levels that start without their own
    heading (6 -> 6.1.1, or 2 -> 2.5.1 when 2.1-2.5 have no number in the text)
    cost a penalty each.
    """
    shared = 0
    while shared < min(len(previous), len(current)) and previous[shared] == current[shared]:
        shared += 1
    if shared == len(current):
        return None  # the same number, or one of its parents
    # The level where current moves on, and the number it moves on from.
    start = previous[shared] if shared < len(previous) else 0
    gap = current[shared] - start - 1
    deeper = current[shared + 1 :]
    if gap < 0 or len(deeper) > _MAX_SKIPPED_LEVELS or any(part != 1 for part in deeper):
        return None
    return min(gap, _MAX_GAP_PENALTY_STEPS) * _GAP_PENALTY + len(deeper) * _SKIPPED_LEVEL_PENALTY


def _start_penalty(number: tuple[int, ...]) -> float:
    """A document's outline normally starts at the top level ("1" or "6")."""
    return (len(number) - 1) * _DEEP_START_PENALTY


def select_outline(candidates: Sequence[Candidate]) -> list[Heading]:
    """The chain of candidates with the highest total score that forms a valid outline."""
    if not candidates:
        return []
    best: list[float] = []
    previous: list[int | None] = []
    for i, candidate in enumerate(candidates):
        value = candidate.score - _start_penalty(candidate.number)
        link: int | None = None
        for j in range(max(0, i - _MAX_LOOKBACK), i):
            if candidates[j].index + candidates[j].consumed > candidate.index:
                continue  # overlapping blocks (a number alone and its title)
            penalty = step_penalty(candidates[j].number, candidate.number)
            if penalty is None:
                continue
            chained = best[j] + candidate.score - penalty
            if chained > value:
                value, link = chained, j
        best.append(value)
        previous.append(link)

    end: int | None = max(range(len(candidates)), key=best.__getitem__)
    chain: list[Candidate] = []
    while end is not None:
        chain.append(candidates[end])
        end = previous[end]
    chain.reverse()
    return [
        Heading(
            index=c.index,
            consumed=c.consumed,
            number=".".join(str(part) for part in c.number),
            level=len(c.number),
            title=c.title,
        )
        for c in chain
    ]
