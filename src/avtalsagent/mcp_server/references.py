"""A section's references, with the files and sections they point to that the tools may show.

What:
    `section_references` reads the references step 4 found in one section
    ("punkt 6.21.9", "bilaga Priser"): for each, the text as written, its
    kind, its status and its targets. A target is a section (`TargetRef` with
    the fields of `results.SectionRef`) or a whole file (the section fields
    None). Targets the tools may not show are counted, not listed.
    `file_targets` gives the whole-file `TargetRef` of files, which
    `find_amendments` also uses.

Why:
    `read_section` returns a section with its references and
    `resolve_reference` follows them; both show them the same way. A
    reference must not open a way around the quarantine: a target in a
    held-back file or section is left out, as in the search, and the count
    tells the model that the list it sees is not the whole list.

How:
    Two queries on step 4's tables: the section's references in the order
    they appear in its text (`document_reference`), and their targets in
    stored order (`reference_target`). Each target is checked against the
    visibility the tool loaded; the visible ones get their citation fields
    from `results.section_refs` (sections) or from `document_metadata` and
    `document_scope` (whole files). A target stored once per agreement page
    is listed once, or counted once when it is held back: the count is of
    distinct targets per reference, and the total sums those counts. The
    filter on the reference's text is case-insensitive and done in Python,
    so `%` or `_` in it mean nothing special.
"""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.mcp_server.results import section_refs
from avtalsagent.mcp_server.visibility import Visibility


class TargetRef(BaseModel):
    """What a reference points to: a section, or a whole file (section fields None)."""

    sha256: str
    file_title: str
    document_type: str
    page_titles: list[str]
    section_position: int | None
    section_number: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None


class SectionReference(BaseModel):
    """A reference in a section's text and the targets the tools may show."""

    raw: str  # as written, e.g. "punkt 6.21.9"
    kind: str  # `ReferenceKind` value, e.g. "section_number"
    status: str  # `ReferenceStatus` value, e.g. "resolved"
    targets: list[TargetRef]
    held_back_targets: int  # targets left out: held back or not indexed


@dataclass(frozen=True)
class SectionReferences:
    references: list[SectionReference]
    held_back_targets: int  # summed over `references`


def section_references(
    session: Session,
    visibility: Visibility,
    sha256: str,
    section_position: int,
    containing: str | None = None,
) -> SectionReferences:
    """The references in one section, or those whose text contains `containing` (any case)."""
    reference = models.DocumentReference
    rows = session.execute(
        select(reference.id, reference.raw, reference.kind, reference.status)
        .where(reference.sha256 == sha256, reference.section_position == section_position)
        .order_by(reference.char_start, reference.id)
    ).all()
    if containing is not None:
        wanted = containing.casefold()
        rows = [row for row in rows if wanted in row.raw.casefold()]
    if not rows:
        return SectionReferences(references=[], held_back_targets=0)

    target = models.ReferenceTarget
    targets: defaultdict[int, list[tuple[str, int | None]]] = defaultdict(list)
    for reference_id, target_sha256, target_section in session.execute(
        select(target.reference_id, target.target_sha256, target.target_section_position)
        .where(target.reference_id.in_([row.id for row in rows]))
        .order_by(target.reference_id, target.position)
    ):
        targets[reference_id].append((target_sha256, target_section))

    shown = {
        (sha, section)
        for pairs in targets.values()
        for sha, section in pairs
        if (
            visibility.shows_file(sha)
            if section is None
            else visibility.shows_section(sha, section)
        )
    }
    sections = section_refs(session, {(sha, s) for sha, s in shown if s is not None})
    files = file_targets(session, {sha for sha, s in shown if s is None})

    references: list[SectionReference] = []
    for row in rows:
        visible: list[TargetRef] = []
        held_back: set[tuple[str, int | None]] = set()
        for sha, section in targets[row.id]:
            found: TargetRef | None = None
            if (sha, section) in shown:
                if section is None:
                    found = files.get(sha)
                elif (ref := sections.get((sha, section))) is not None:
                    found = TargetRef(**ref.model_dump())
            if found is None:  # not shown; or shown but without its rows, which never happens
                held_back.add((sha, section))  # once, even if found through two pages
            elif found not in visible:  # the same target found through two pages
                visible.append(found)
        references.append(
            SectionReference(
                raw=row.raw,
                kind=row.kind,
                status=row.status,
                targets=visible,
                held_back_targets=len(held_back),
            )
        )
    return SectionReferences(
        references=references,
        held_back_targets=sum(item.held_back_targets for item in references),
    )


def file_targets(session: Session, sha256s: Collection[str]) -> dict[str, TargetRef]:
    """A whole-file `TargetRef` for each file that has metadata and a scope, by sha256."""
    if not sha256s:
        return {}
    metadata, scope = models.DocumentMetadata, models.DocumentScope
    rows = session.execute(
        select(metadata.sha256, metadata.title, metadata.document_type, scope.page_titles)
        .join(scope, scope.sha256 == metadata.sha256)
        .where(metadata.sha256.in_(list(sha256s)))
    )
    return {
        row.sha256: TargetRef(
            sha256=row.sha256,
            file_title=row.title,
            document_type=row.document_type,
            page_titles=list(row.page_titles),
            section_position=None,
            section_number=None,
            section_title=None,
            page_start=None,
            page_end=None,
        )
        for row in rows
    }
