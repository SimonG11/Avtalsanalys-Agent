"""The citation check: each quote word for word in its section, each source used once.

What:
    `check_citations(draft, sections)` checks a `FinalAnswer` against the
    sections it cites and gives a `CitationReport`: the problems, in Swedish
    for the model, and the answer's `Citation`s, each marked verified or
    not. `normalise` is the text form quotes are compared in.

Why:
    An answer about an agreement is only worth as much as its sources, so
    the check proves the one thing that can be proved without judgement:
    the quoted words are in the cited section (ADR 0013). Quoting the
    section, not a search hit's snippet, means the user can find the words
    on the page the web app opens. The fields a citation shows (file, page,
    section) are copied from the section, never from the model, so a source
    cannot carry an invented name or page. The model gets each problem
    named by its source [n], so a new attempt can fix exactly that one.

How:
    The text: every [n] in the text (or n in [n, m]) has a source with id
    n, every source is used in the text, and the ids are distinct and run
    1..N. Each
    source: its (sha256, section_position) is a section the caller could
    read (held-back sections are not), and its quote, without surrounding
    quote marks or an ellipsis, is a substring of the section's text once
    both are normalised (`normalise`): NFKC; typographic quote marks and
    dashes as ASCII; soft hyphens removed, with the line break after one;
    whitespace collapsed; case folded. A quote shorter than
    `MIN_QUOTE_LENGTH` after that fails unless it is the whole section, so
    "tre månader" alone proves nothing. A citation's page is the section's
    first page, also when the quote is on a later one. Its page title is
    the one the draft chose when the section has it, else the section's
    first (a file shared by several agreement pages has them all).
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from avtalsagent.agent.schemas import Citation, DraftCitation, FinalAnswer
from avtalsagent.agent.sections import CitedSection

# Characters, after normalisation, a quote must have unless it is the whole section.
MIN_QUOTE_LENGTH = 15

# The quote marks and dashes of Swedish typography, as their ASCII forms. NFKC turns the
# non-breaking hyphen (U+2011) into U+2010 and the ellipsis into "...", so those are covered.
_ASCII = str.maketrans(
    {
        **dict.fromkeys("“”„«»", '"'),  # “ ” „ « »
        **dict.fromkeys("‘’‚‹›", "'"),  # ‘ ’ ‚ ‹ ›
        **dict.fromkeys("‐‑‒–—−", "-"),  # ‐ ‑ ‒ – — −
    }
)
# A soft hyphen marks where a word may break; at a line break the word goes on after it.
_SOFT_HYPHEN = re.compile("­\\s*")
_WHITESPACE = re.compile(r"\s+")
# What may surround a quote without being part of it: space, quote marks and an ellipsis.
# Matched only at the start, of the quote and of the quote reversed (each part reads the same
# backwards): a search for it at the end would try every position, slow on a long inner space.
_EDGE = re.compile("(?:\\s|[\"'“”„«»‘’‚‹›]|…|\\.\\.\\.)+")
# A reference in the answer's text to sources: [1], or [1, 2] as the web app also reads it.
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True)
class CitationReport:
    """The outcome of the check: no problems means the answer passed."""

    problems: list[str]  # in Swedish, for the model: each names its source [n]
    citations: list[Citation]  # every source of the draft, in its order


def normalise(text: str) -> str:
    """`text` in the form quotes are compared in (see the module's How)."""
    text = unicodedata.normalize("NFKC", text)
    text = _SOFT_HYPHEN.sub("", text).translate(_ASCII)
    return _WHITESPACE.sub(" ", text).strip().casefold()


def check_citations(
    draft: FinalAnswer, sections: Mapping[tuple[str, int], CitedSection | None]
) -> CitationReport:
    """Check `draft`'s sources against `sections`, keyed by (sha256, section_position).

    A key that is missing or None is a section that does not exist or may
    not be shown.
    """
    problems = _text_problems(draft)
    citations = []
    for source in draft.citations:
        section = sections.get((source.sha256, source.section_position))
        problem = _quote_problem(source, section)
        if problem is not None:
            problems.append(problem)
        citations.append(_citation(source, section, verified=problem is None))
    return CitationReport(problems=problems, citations=citations)


def _text_problems(draft: FinalAnswer) -> list[str]:
    """The text's [n] markers against the sources' ids."""
    ids = [source.id for source in draft.citations]
    used = {int(number) for marker in _MARKER.findall(draft.text) for number in marker.split(",")}
    problems = [
        f"Hänvisningen [{number}] i texten har ingen källa med id {number}."
        for number in sorted(used - set(ids))
    ]
    problems += [
        f"Källa [{number}] används inte i texten: sätt [{number}] efter påståendet den "
        "stöder, eller ta bort källan."
        for number in dict.fromkeys(ids)
        if number not in used
    ]
    problems += [
        f"Flera källor har id {number}: ge varje källa ett eget id."
        for number in sorted({number for number in ids if ids.count(number) > 1})
    ]
    distinct = sorted(set(ids))
    if distinct != list(range(1, len(distinct) + 1)):
        listed = ", ".join(str(number) for number in distinct)
        problems.append(f"Källornas id ska vara 1–{len(distinct)} utan luckor, men är {listed}.")
    return problems


def _quote_problem(source: DraftCitation, section: CitedSection | None) -> str | None:
    """What is wrong with one source's section or quote; None when nothing is."""
    if section is None:
        return (
            f"Källa [{source.id}]: det finns inget avsnitt att läsa på plats "
            f"{source.section_position} i dokumentet {source.sha256[:12]}…, eller så hålls "
            "det tillbaka. Kopiera sha256 och section_position från read_section."
        )
    quote = normalise(_trim(source.quote))
    text = normalise(section.text)
    where = _section_name(section)
    if not quote or (len(quote) < MIN_QUOTE_LENGTH and quote != text):
        return (
            f"Källa [{source.id}]: citatet är för kort (minst {MIN_QUOTE_LENGTH} tecken). "
            f"Citera en längre sammanhängande del av {where}."
        )
    if quote not in text:
        return (
            f"Källa [{source.id}]: citatet finns inte ordagrant i {where}. Kopiera det ur "
            "texten från read_section, utan utelämningar och utan egna ord."
        )
    return None


def _citation(source: DraftCitation, section: CitedSection | None, *, verified: bool) -> Citation:
    """The answer's citation: the model's id and quote, the rest from the section."""
    if section is None:  # nothing to copy: the source stays, marked as not verified
        return Citation(
            id=source.id,
            sha256=source.sha256,
            file_title="",
            page_title=None,
            section_number=None,
            section_title="",
            page=None,
            quote=_trim(source.quote) or source.quote,
            verified=False,
        )
    return Citation(
        id=source.id,
        sha256=section.sha256,
        file_title=section.file_title,
        page_title=_page_title(source, section),
        section_number=section.section_number,
        section_title=section.section_title,
        page=section.page_start,
        quote=_trim(source.quote) or source.quote,  # never empty, for the web app
        verified=verified,
    )


def _page_title(source: DraftCitation, section: CitedSection) -> str | None:
    """The agreement page the draft chose, if the section has it; else the section's first."""
    if source.page_title in section.page_titles:
        return source.page_title
    return section.page_titles[0] if section.page_titles else None


def _trim(quote: str) -> str:
    """The quote without surrounding space, quote marks or ellipsis: the words to find."""
    return _strip_start(_strip_start(quote)[::-1])[::-1]


def _strip_start(text: str) -> str:
    edge = _EDGE.match(text)
    return text[edge.end() :] if edge else text


def _section_name(section: CitedSection) -> str:
    """How a problem names the section: its number and title, or its title."""
    if section.section_number:
        return f"avsnitt {section.section_number} ({section.section_title})"
    return f"avsnittet ”{section.section_title}”"
