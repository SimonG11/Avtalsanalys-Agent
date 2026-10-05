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
    Each candidate gets a score: higher when the parser marked it as a heading,
    when it is short like a title and when its number is in the document's
    own table of contents, lower for list items. A dynamic
    programme then finds the chain of candidates with the highest total
    score in which each number may follow the previous one: the first child
    (6.6 -> 6.6.1), the next number at the same or a higher level
    (6.6.8 -> 6.6.9, 6.7, 7) or, with a penalty, missing numbers and levels
    without their own heading (6 -> 6.1.1; 1.3 -> 2.4 only when the table of
    contents lists 2.4). If the best chain leaves out numbers the contents
    lists, the chain with the most listed numbers is chosen instead. Pure
    functions on `Block`s, so every rule has a test.
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
# The rest of a heading that the layout model put in a block of its own, also across a
# page break: "5.4.1.4 Terroristbrott eller brott med anknytning till" + "terroristverksamhet".
_CONTINUATION = re.compile(r"^[a-zåäö]")
_MAX_CONTINUATION_CHARS = 80
# A contents line without its dot leaders and page number.
_LEADERS = re.compile(r"\.{3,}|…+")
_PAGE_AT_END = re.compile(r"\s+\d{1,3}$")

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
    listed: bool = False  # the number is in the document's own table of contents


@dataclass(frozen=True)
class ContentsEntry:
    """A line of the document's own table of contents."""

    number: tuple[int, ...] | None  # None when the line has no number in the text
    title: str


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


def listed_numbers(blocks: Sequence[Block]) -> frozenset[tuple[int, ...]]:
    """The section numbers in the document's own table of contents."""
    return frozenset(
        parse_number(match["number"])
        for index in toc_entries(blocks)
        for line in blocks[index].text.splitlines()
        if (match := _NUMBERED.match(line.strip()))
    )


def find_candidates(
    blocks: Sequence[Block], listed: frozenset[tuple[int, ...]] | None = None
) -> list[Candidate]:
    """Every block outside the table of contents that starts like a numbered heading.

    `listed` are the numbers in the document's table of contents. Step 3 reads
    them before it removes the contents; by default they are read from `blocks`.
    """
    toc = toc_entries(blocks)
    # Numbers listed in the document's own table of contents are very likely headings.
    in_toc = listed_numbers(blocks) if listed is None else listed
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
        rest = blocks[index + consumed] if index + consumed < len(blocks) else None
        if (
            rest is not None
            and blocks[index + consumed - 1].kind is BlockKind.HEADING
            and rest.kind is BlockKind.HEADING
            and _CONTINUATION.match(rest.text.strip())
            and len(rest.text) <= _MAX_CONTINUATION_CHARS
        ):
            title = f"{title} {' '.join(rest.text.split())}"
            consumed += 1
        if not _looks_like_title(title):
            continue
        number = parse_number(number_text)
        # Sections are numbered from 1; "17.00" and "1.0" are times and versions.
        if any(part > _MAX_NUMBER_PART or part == 0 for part in number):
            continue
        is_listed = number in in_toc
        score = _score(block, title) + (_IN_TOC_BONUS if is_listed else 0.0)
        candidates.append(Candidate(index, consumed, number, title, score, is_listed))
    return candidates


def contents_entries(blocks: Sequence[Block]) -> list[ContentsEntry]:
    """The lines of the document's table of contents in order, without page numbers.

    A title wrapped over two lines has its page number on the second line only,
    so a line without a page number is joined with the next one.
    """
    entries: list[ContentsEntry] = []
    pending = ""
    for index in sorted(toc_entries(blocks)):
        for raw in blocks[index].text.splitlines():
            line = " ".join(_LEADERS.sub(" ", raw.replace("|", " ")).split())
            if not line:
                continue
            line = f"{pending} {line}".strip()
            if not _PAGE_AT_END.search(line):
                pending = line
                continue
            pending = ""
            line = _PAGE_AT_END.sub("", line)
            match = _NUMBERED.match(line)
            if match is None:
                entries.append(ContentsEntry(None, line))
            else:
                title = " ".join(match["title"].split())
                entries.append(ContentsEntry(parse_number(match["number"]), title))
    return entries


def infer_numbers(entries: Sequence[ContentsEntry]) -> list[tuple[int, ...] | None]:
    """Numbers for the contents entries, also for those printed without one.

    Some documents print one level's numbers as images, so "2.1 Avropsberättigade"
    is "Avropsberättigade" in the text layer, in the contents and in the body.
    The numbers can be recovered from the numbered entries around them: the
    entries between "2 IT-konsulttjänster" and "2.5.1 Delområden" end with 2.5
    and count back from it (2.1 ... 2.5). A run is only numbered when its first
    number follows the entry before it and its last is followed by the entry
    after it without a gap. Once a level is known to be printed as images, a
    run before a new chapter or at the end gets numbers at that level
    (between "1 Om vägledningen" and "2 IT-konsulttjänster": 1.1, 1.2). Other
    unnumbered entries get None.
    """
    numbers = [entry.number for entry in entries]
    runs: list[tuple[int, int]] = []  # [start, end) of each run of unnumbered entries
    start = None
    for position, number in enumerate([*numbers, ()]):
        if number is None and start is None:
            start = position
        elif number is not None and start is not None:
            runs.append((start, position))
            start = None
    image_level = None
    for second_pass in (False, True):
        for start, end in runs:
            before = numbers[start - 1] if start > 0 else None
            after = numbers[end] if end < len(numbers) else None
            if before is None or numbers[start] is not None:
                continue
            if after is not None and len(after) > 1 and not second_pass:
                last = after[:-1]
                run = [(*last[:-1], last[-1] - (end - 1 - i)) for i in range(start, end)]
            elif second_pass and image_level is not None:
                first = _successor(before, image_level)
                if first is None:
                    continue
                run = [(*first[:-1], first[-1] + i) for i in range(end - start)]
            else:
                continue
            fits = step_penalty(before, run[0]) == 0.0 and (
                after is None or step_penalty(run[-1], after) == 0.0
            )
            if all(part > 0 for number in run for part in number) and fits:
                numbers[start:end] = run
                image_level = image_level or len(run[0])
    return numbers


def _successor(number: tuple[int, ...], level: int) -> tuple[int, ...] | None:
    """The first number at `level` after `number`: 2 -> 2.1 and 2.5.3 -> 2.6 at level 2."""
    if len(number) >= level:
        return (*number[: level - 1], number[level - 1] + 1)
    if len(number) == level - 1:
        return (*number, 1)
    return None


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


def step_penalty(
    previous: tuple[int, ...], current: tuple[int, ...], listed: bool = False
) -> float | None:
    """The penalty for `current` following `previous` in an outline; None if it cannot.

    `current` must move on at some level: the first child (6.6 -> 6.6.1) or the
    next number at the same or a higher level (6.6.8 -> 6.6.9, 6.7, 7). Missing
    numbers in between (6.2 -> 6.4) and levels that start without their own
    heading (6 -> 6.1.1, or 2 -> 2.5.1 when 2.1-2.5 have no number in the text)
    cost a penalty each. A level without its own heading starts at 1, unless
    the number is `listed` in the document's table of contents: a template
    with deleted sections can go from 1.3 to 2.4.
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
    if gap < 0 or len(deeper) > _MAX_SKIPPED_LEVELS:
        return None
    missing_below = sum(part - 1 for part in deeper)
    if missing_below and not listed:
        return None
    gap += missing_below
    return min(gap, _MAX_GAP_PENALTY_STEPS) * _GAP_PENALTY + len(deeper) * _SKIPPED_LEVEL_PENALTY


def _start_penalty(number: tuple[int, ...]) -> float:
    """A document's outline normally starts at the top level ("1" or "6")."""
    return (len(number) - 1) * _DEEP_START_PENALTY


def select_outline(candidates: Sequence[Candidate]) -> list[Heading]:
    """The chain of candidates with the highest total score that forms a valid outline.

    Numbers quoted from another document can outscore a real heading: after
    "5 Tekniska krav" a document quotes requirements 4.6.1-4.6.6, and the chain
    4, 4.6.1 ... 4.6.6, 6 has more headings than 4, 5, 6. So when the best chain
    leaves out numbers that the table of contents lists, the chain is chosen
    again with as many listed numbers as possible, and only then the highest
    score. The second chain is used if it has more listed numbers.
    """
    if not candidates:
        return []
    chain = _best_chain(candidates, listed_bonus=0.0)
    listed = {c.number for c in candidates if c.listed}
    if listed - {c.number for c in chain}:
        # A bonus larger than all scores together: one more listed number always wins.
        second = _best_chain(candidates, listed_bonus=1.0 + sum(c.score for c in candidates))
        if _listed_count(second) > _listed_count(chain):
            chain = second
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


def _best_chain(candidates: Sequence[Candidate], listed_bonus: float) -> list[Candidate]:
    """The highest scoring valid chain, with `listed_bonus` added for listed numbers."""

    def score(candidate: Candidate) -> float:
        return candidate.score + (listed_bonus if candidate.listed else 0.0)

    best: list[float] = []
    previous: list[int | None] = []
    for i, candidate in enumerate(candidates):
        value = score(candidate) - _start_penalty(candidate.number)
        link: int | None = None
        for j in range(max(0, i - _MAX_LOOKBACK), i):
            if candidates[j].index + candidates[j].consumed > candidate.index:
                continue  # overlapping blocks (a number alone and its title)
            penalty = step_penalty(candidates[j].number, candidate.number, candidate.listed)
            if penalty is None:
                continue
            chained = best[j] + score(candidate) - penalty
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
    return chain


def _listed_count(chain: Sequence[Candidate]) -> int:
    return len({c.number for c in chain if c.listed})
