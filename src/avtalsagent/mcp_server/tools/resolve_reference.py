"""resolve_reference: where the references in a section point.

What:
    `resolve_reference` returns a section's citation fields and its
    references, or only those whose text contains `reference`: each with
    its text, kind, status and the targets the tools may show, plus how many
    targets are held back.

Why:
    Agreements answer through references: "vite enligt punkt 6.21.9",
    "priser enligt bilaga Priser". Step 4 resolved them against the files of
    the same agreement page (M4), so the agent follows a reference to the
    right file and section instead of searching for the words and perhaps
    landing in another agreement's copy. The status tells the model when
    there is nothing to follow (a law, a file that was never published) or
    when it must choose between candidates.

How:
    `read_section.find_section` finds the section as `read_section` does,
    and `references.section_references` reads its references with the
    visible targets; the filter is a case-insensitive substring of the
    reference's text. No matching reference is an empty list, not an error.
"""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from avtalsagent.mcp_server.arguments import Reference, SectionNumber, SectionPosition, Sha256
from avtalsagent.mcp_server.references import SectionReference, section_references
from avtalsagent.mcp_server.results import SectionRef, section_refs
from avtalsagent.mcp_server.tools.read_section import check_section_arguments, find_section
from avtalsagent.mcp_server.visibility import load_visibility


class ReferenceResult(BaseModel):
    """A section and the references in it, with the targets the tools may show."""

    section: SectionRef
    references: list[SectionReference]
    held_back_targets: int  # targets left out, summed over the references


def resolve_reference(
    session: Session,
    sha256: Sha256,
    section_number: SectionNumber = None,
    section_position: SectionPosition = None,
    reference: Reference = None,
) -> ReferenceResult:
    """Följ hänvisningarna i ett avsnitt, t.ex. 'punkt 6.21.9' eller 'bilaga Priser'.

    Ange avsnittet som till read_section och, om du bara vill ha en hänvisning, en del av
    dess text i reference. Varje hänvisning har en status: resolved (målet hittades),
    ambiguous (flera möjliga mål, alla listas), self (dokumentet självt), number_missing
    eller title_missing (måldokumentet saknar avsnittet; dokumentet anges som mål),
    not_published (dokumentet finns inte bland de hämtade), external (lag eller standard),
    list_item (en punkt i en lista) eller placeholder (ett mallfält). Läs ett mål med
    read_section. Ett mål utan section_position är ett helt dokument: öppna det med
    get_outline och läs sedan ett avsnitt i det med read_section. held_back_targets räknar
    mål som hålls tillbaka och därför saknas.
    """
    check_section_arguments(section_number, section_position)
    visibility = load_visibility(session)
    row = find_section(session, visibility, sha256, section_number, section_position)
    key = (row.sha256, row.position)
    found = section_references(session, visibility, row.sha256, row.position, reference)
    return ReferenceResult(
        section=section_refs(session, [key])[key],  # a visible section has both rows
        references=found.references,
        held_back_targets=found.held_back_targets,
    )
