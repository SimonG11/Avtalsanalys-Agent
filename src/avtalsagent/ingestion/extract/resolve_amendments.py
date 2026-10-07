"""Ingestion step 4: references in an amendment to the annexes it amends.

What:
    `covered_files` gives the files an amendment amends, read from its title:
    Microsoft's "Bilaga 4 Tillägg och förtydliganden till bilaga 4.1" amends the
    file with annex number 4.1 on the same page. Two rules look there first:
    - R1a `resolve_amended_number`: a section number with no document named with
      it ("Punkt 2a. i Registreringen ersätts med följande").
    - R4a `resolve_amended_title`: a section title the amendment itself does not
      have ('Punkten "Övrigt" under underrubriken "Tvistlösning"'), before the
      other files of the page.

Why:
    An amendment cites the sections it changes in the agreement it amends, not
    its own: 5aab54c5a4b1 §pos0 "B. Punkten 3.b. ... i Avtalet ändras härmed" is
    section 3 of the Enterprise agreement 5.1, while R1 found the amendment's own
    list item 3 ("3. Med undantag för kostnadsfria provperioder ..."). And
    'Punkten "Övrigt"' in d54ed0900be5 was AMBIGUOUS among the four files of the
    page with a section "Övrigt." (annexes 4.1, 5.1, 6.1 and 7.1). The agent's
    find_amendments must know which section an amendment changes.

How:
    The title names the annexes after "till bilaga", "bilagan", "bilagor" or
    "bilagorna": one number ("4.1") or a range with one main number ("5.1-5.4").
    On each page that links the amendment, the files whose `annex_number` lies in
    it are covered. Only the amendment's own pages count: IBM's page also has
    files with annex numbers 5.2 and 5.3. Each page's link title of the amendment
    is read, since a title can differ between pages.
    R1a looks up the number as it is written, and else without its letter at the
    end: "Punkt 2a" is item a of section 2 (aba1280d90be §2 "Beställningskrav. a.
    Minsta beställningskrav."), and "Punkten 3.b." gives the key "3". R4a takes
    the longest heading the title starts with, as R4 does in the other files of
    the page. Both decide by `page_outcome`: RESOLVED with one candidate per page,
    AMBIGUOUS with several (5aab54c5a4b1 amends four annexes that each have a
    section 2 and a section 3). When no covered file has the number or the title, the rules give
    None and the reference goes on to R1 in the amendment itself, or to R4 in the
    other files of the page.
    Measured on the pilot: the five Microsoft amendments cover 9 files. R1a
    decides 2 references, both AMBIGUOUS among 5.1-5.4, and R4a 5, of which 4
    resolve (docs/steg/04-extraktion.md).
"""

import re
from collections.abc import Iterable

from avtalsagent.domain.extracted import DocumentType, Reference, ReferenceMention
from avtalsagent.ingestion.extract.resolve_in_document import DocumentIndex
from avtalsagent.ingestion.extract.resolve_on_page import (
    Candidate,
    Corpus,
    by_page,
    page_outcome,
)

# The annexes an amendment's link title names: 5aab54c5a4b1 "Bilaga 5 Tillägg och
# förtydligande till bilagorna 5.1-5.4", 386122e82b7b "Bilaga 7 Tillägg och förtydliganden
# till bilaga 7.1".
_AMENDED_ANNEXES = re.compile(
    r"\btill\s+bilag(?:a|an|or|orna)\s+(?P<first>\d{1,2}(?:\.\d{1,2})?)"
    r"(?:\s*[-–]\s*(?P<last>\d{1,2}(?:\.\d{1,2})?))?(?![\d.])",
    re.IGNORECASE,
)
# A letter at the end of a section number: "2a" is item a of section 2.
_ITEM_LETTER = re.compile(r"\.?[a-z]$")


def covered_files(corpus: Corpus, index: DocumentIndex) -> dict[str, list[str]]:
    """The files an amendment amends, on each page that links it; empty lists for any other.

    A file is covered when its annex number lies in the range the amendment's link title
    on that page names ("till bilagorna 5.1-5.4").
    """
    if index.document.metadata.document_type is not DocumentType.AMENDMENT:
        return {page: [] for page in corpus.pages_of(index)}
    covered: dict[str, list[str]] = {}
    for page in corpus.pages_of(index):
        ranges = [
            (match["first"], match["last"] or match["first"])
            for link in index.document.links
            if link.page_url == page and (match := _AMENDED_ANNEXES.search(link.title))
        ]
        covered[page] = list(
            dict.fromkeys(
                link.sha256
                for link in corpus.links_on(page)
                if any(
                    _in_range(corpus.files[link.sha256].document.metadata.annex_number, *bounds)
                    for bounds in ranges
                )
            )
        )
    return covered


def _in_range(number: str | None, first: str, last: str) -> bool:
    """Whether an annex number lies in a range with one main number: "5.2" in 5.1-5.4.

    Annex numbers are digits and dots, as `document_type.annex_number` reads them.
    """
    if number is None:
        return False
    parts, low, high = (tuple(int(part) for part in n.split(".")) for n in (number, first, last))
    if not len(parts) == len(low) == len(high) or not parts[:-1] == low[:-1] == high[:-1]:
        return False
    return low[-1] <= parts[-1] <= high[-1]


def resolve_amended_number(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference | None:
    """R1a: a section number in an amendment, in the files it amends.

    5aab54c5a4b1 §pos0: "A. Punkt 2a. i Registreringen ersätts med följande" resolves to
    section 2 of each of the four registrations 5.1-5.4 (AMBIGUOUS). None when the file is
    not an amendment that covers files, or no covered file has the number.
    """
    covered = covered_files(corpus, index)
    keys = tuple(dict.fromkeys((mention.key, _ITEM_LETTER.sub("", mention.key))))

    def select(page: str) -> Iterable[Candidate]:
        for sha256 in covered.get(page, ()):
            file = corpus.files[sha256]
            position = next((p for key in keys if (p := file.numbered(key)) is not None), None)
            if position is not None:
                yield sha256, position

    return page_outcome(index, mention, by_page(corpus, index, select), "R1a")


def resolve_amended_title(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference | None:
    """R4a: a section title an amendment does not have, in the files it amends.

    d54ed0900be5 §pos6: 'B. Punkten "Övrigt" under underrubriken "Tvistlösning" i Microsoft
    Business and Services Avtal ändras härmed' resolves to 10 "Övrigt." of annex 4.1
    (005469cd3990). On each page the covered files with the longest heading the title
    starts with count. None when no covered file has it.
    """
    covered = covered_files(corpus, index)

    def select(page: str) -> list[Candidate]:
        matches = [
            (len(match[0]), sha256, match[1])
            for sha256 in covered.get(page, ())
            if (match := corpus.files[sha256].heading_match(mention.key))
        ]
        longest = max((length for length, _, _ in matches), default=0)
        return [
            (sha256, position)
            for length, sha256, positions in matches
            if length == longest
            for position in positions
        ]

    return page_outcome(index, mention, by_page(corpus, index, select), "R4a")
