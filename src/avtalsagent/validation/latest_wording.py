"""The latest-wording rule: an answer that cites an amended section cites the amendment too.

What:
    `check_latest_wording(draft, citations, amendments)` checks each section
    a `FinalAnswer` cites, and whose citation passed, against the sections
    that change it, and gives a `WordingReport`: the problems, in Swedish
    for the model, the reservations for the user if the draft is given as
    it is, and whether the amendments of a cited section could not be read.

Why:
    Agreements change after they are signed, and a procurement's terms
    while it is open. An answer built on the replaced wording is wrong even
    when every quote is exact: punkt 3.2 of the tender documents for
    IT-drift Mindre (e394ae5dfd2c §3.2) says that the seven (7) best tenders
    are accepted, and Kammarkollegiet's answer in the questions log
    (7a765d649e25 §9, 2024-02-20) corrects it to eight (8); Bilaga 4
    (d54ed0900be5, "Amendment ID CTM") puts Swedish law where Microsoft's
    agreement (005469cd3990 §10) has Irish. Which wording an answer is built
    on takes judgement, but whether it cites the amendment does not: an
    answer that quotes only the old wording has not shown the user the new
    one. So the rule asks for the citation, and the reviewer, who reads both
    sections, judges what the answer says (ADR 0015). The amendments are
    read again through avtal-mcp's `find_amendments`, never taken from the
    message history.

How:
    `amendments` maps each cited (sha256, section_position) whose citation
    passed to what the reader gave: the sections that change it or its
    file, or None when they could not be read (a missing key is the same).
    A section's amendments are those with status "resolved" whose amended
    position is the cited section's own. When it has some, the draft must
    also cite, with a citation that passed, at least one of their amending
    sections; any one will do, not necessarily the newest. Otherwise there
    is one problem per cited section, named by its first source [n], which
    names up to `MAX_NAMED` amendments newest first (undated last) with the
    sha256 and section_position that `read_section` takes, and one
    reservation that names them for the user. A cited amending section is a
    cited section like any other: the rule applies to it on its own, so an
    amendment that is amended in turn needs its own amendment cited. Not
    checked, and left to the agent, who sees them in `find_amendments`:
    ambiguous references (the change may be to another file: 5aab54c5a4b1
    changes "punkt 2a" of four annexes that each have a section 2), changes
    to the whole file, and amendments the tools hold back, which the tool
    only counts. Amendments that could not be read give no problem, which
    no new attempt could fix, but one reservation naming their sources.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.schemas import Citation, FinalAnswer
from avtalsagent.domain.extracted import ReferenceStatus

# The amendments a problem or a reservation names; the rest are counted.
MAX_NAMED = 3


@dataclass(frozen=True)
class WordingReport:
    """The outcome of the rule: no problems means the draft passed it."""

    problems: list[str]  # in Swedish, for the model: each names its source [n]
    reservations: list[str]  # in Swedish, for the user, if the draft is given as it is
    unread: bool  # the amendments of a cited section could not be read


def check_latest_wording(
    draft: FinalAnswer,
    citations: Sequence[Citation],
    amendments: Mapping[tuple[str, int], list[AmendmentInfo] | None],
) -> WordingReport:
    """Check `draft`'s passed sources against their sections' `amendments` (see the How)."""
    cited: dict[tuple[str, int], Citation] = {}  # each passed section's first source
    for source, citation in zip(draft.citations, citations, strict=True):
        if citation.verified:
            cited.setdefault((source.sha256, source.section_position), citation)

    problems: list[str] = []
    reservations: list[str] = []
    unread: list[int] = []
    for (sha256, position), citation in cited.items():
        found = amendments.get((sha256, position))
        if found is None:
            unread.append(citation.id)
            continue
        changes = _changes(found, position)
        if not changes or any((c.sha256, c.section_position) in cited for c in changes):
            continue
        problems.append(_problem(citation, changes))
        reservations.append(
            f"Källa [{citation.id}] kan bygga på en lydelse som har ändrats senare: "
            f"{_listed(changes, for_model=False)}."
        )
    if unread:
        reservations.append(_unread_note(unread))
    return WordingReport(problems=problems, reservations=reservations, unread=bool(unread))


def _changes(found: Sequence[AmendmentInfo], position: int) -> list[AmendmentInfo]:
    """The resolved changes to the section at `position`, each once, newest first."""
    changes: dict[tuple[str, int], AmendmentInfo] = {}
    for change in found:
        if change.status == ReferenceStatus.RESOLVED and change.amended_position == position:
            changes.setdefault((change.sha256, change.section_position), change)
    return sorted(
        changes.values(),
        key=lambda change: (
            change.dated is None,
            -change.dated.toordinal() if change.dated else 0,
        ),
    )


def _problem(citation: Citation, changes: Sequence[AmendmentInfo]) -> str:
    """What the model is told: the source, its amendments and how to use them."""
    cited = _section_name(citation.file_title, citation.section_number, citation.section_title)
    one = len(changes) == 1
    return (
        f"Källa [{citation.id}] ({cited}) har ändrats av {_listed(changes, for_model=True)}. "
        f"Läs {'ändringen' if one else 'ändringarna'} med read_section, bygg svaret på den "
        f"senaste lydelsen och citera {'ändringen' if one else 'den ändring som gäller'}."
    )


def _listed(changes: Sequence[AmendmentInfo], *, for_model: bool) -> str:
    """Up to `MAX_NAMED` amendments by name and date; the model also gets where to read them."""
    named = []
    for change in changes[:MAX_NAMED]:
        details = [change.dated.isoformat()] if change.dated else []
        if for_model:
            details += [f"sha256 {change.sha256}", f"section_position {change.section_position}"]
        name = _section_name(change.file_title, change.section_number, change.section_title)
        named.append(f"{name} ({', '.join(details)})" if details else name)
    listed = "; ".join(named)
    if len(changes) > MAX_NAMED:
        listed += f" och {len(changes) - MAX_NAMED} till"
    return listed


def _section_name(file_title: str, section_number: str | None, section_title: str) -> str:
    """How a problem or a note names a section: its file, and its number and title."""
    if section_number:
        return f"{file_title}, avsnitt {section_number} {section_title}"
    return f"{file_title}, avsnittet ”{section_title}”"


def _unread_note(ids: Sequence[int]) -> str:
    """The note naming the sources whose amendments could not be read."""
    named = ", ".join(f"[{number}]" for number in ids)
    if len(ids) == 1:
        return f"Det gick inte att kontrollera om källa {named} har ändrats."
    return f"Det gick inte att kontrollera om källorna {named} har ändrats."
