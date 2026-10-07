"""read_section: one whole section of a document, with its references.

What:
    `read_section` returns a section found by its number or its position:
    where it is (`results.SectionRef`), its heading path, its whole text and
    the references in it (`references.py`). `check_section_arguments`,
    `require_file` and `find_section` turn the arguments into a visible file
    and section, or into the error the model reads; `get_outline` and
    `resolve_reference` use them too.

Why:
    The search returns the best chunk of a section, and the agent must read
    the whole section before it cites it: the citation check compares each
    quote with the text this tool returns, so the text is exactly as stored.
    A section is named the way the documents name it ("6.21.9"), or by its
    position when it has no number. A search hit and an outline give both,
    so both may be passed; then they must name the same section. A number
    two sections share is an error that lists both, so the model never
    reads the wrong one by chance.

How:
    The file is checked against the visibility (`visibility.py`), then one
    query finds the section by its key (the position, when given) or its
    number, and the section is checked too: a held-back or unindexed file or
    section is a `NotFoundError` that says so. A number is compared after
    removing surrounding spaces and a final dot ("6.21.9."). The citation
    fields come from `results.section_refs`, the references from
    `references.py`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.mcp_server.arguments import SectionNumber, SectionPosition, Sha256
from avtalsagent.mcp_server.errors import AmbiguousError, MissingArgumentError, NotFoundError
from avtalsagent.mcp_server.references import SectionReference, section_references
from avtalsagent.mcp_server.results import SectionRef, section_refs
from avtalsagent.mcp_server.visibility import Visibility, load_visibility


class Section(SectionRef):
    """A whole section: where it is, its text and its references."""

    path: list[str]  # the headings from the top down to this one
    text: str  # exactly as stored: quotes are checked against it
    references: list[SectionReference]
    held_back_targets: int  # reference targets left out: held back or not indexed


def read_section(
    session: Session,
    sha256: Sha256,
    section_number: SectionNumber = None,
    section_position: SectionPosition = None,
) -> Section:
    """Läs ett helt avsnitt: rubrik, sidor, hela texten och avsnittets hänvisningar.

    Ange dokumentets sha256 och section_number eller, för ett avsnitt utan nummer,
    section_position, som i en sökträff eller i get_outline; anger du båda måste de gälla
    samma avsnitt. Läs alltid avsnittet här innan du citerar det: citat kontrolleras mot
    texten detta verktyg returnerar, inte mot sökträffens utdrag. Hänvisningarnas mål läser
    du sedan med read_section. Ett mål utan section_position är ett helt dokument: öppna det
    med get_outline och läs sedan ett avsnitt i det med read_section. För att bara följa en
    viss hänvisning, använd resolve_reference.
    """
    check_section_arguments(section_number, section_position)
    visibility = load_visibility(session)
    row = find_section(session, visibility, sha256, section_number, section_position)
    # A visible section's file has metadata and a scope, so section_refs finds it.
    ref = section_refs(session, [(row.sha256, row.position)])[(row.sha256, row.position)]
    found = section_references(session, visibility, row.sha256, row.position)
    return Section(
        **ref.model_dump(),
        path=list(row.path),
        text=row.text,
        references=found.references,
        held_back_targets=found.held_back_targets,
    )


def check_section_arguments(section_number: str | None, section_position: int | None) -> None:
    """Raise `MissingArgumentError` unless the section's number or position is given."""
    if section_number is None and section_position is None:
        raise _no_section()


def _no_section() -> MissingArgumentError:
    return MissingArgumentError(
        "Ange section_number eller section_position för avsnittet, från en sökträff "
        "eller från get_outline."
    )


def require_file(session: Session, visibility: Visibility, sha256: str) -> None:
    """Return if the tools may show the file; otherwise `NotFoundError` saying why not."""
    if visibility.shows_file(sha256):
        return
    if session.get(models.ParsedFile, sha256) is None:
        raise NotFoundError(
            f"Det finns inget dokument med sha256 {sha256}. Ta sha256 från en sökträff "
            "eller från list_documents."
        )
    if visibility.quarantine.holds(sha256):
        raise NotFoundError(
            f"Dokumentet {sha256[:12]}… hålls tillbaka i granskningen och kan inte läsas. "
            "Sök i de andra dokumenten med search_documents."
        )
    raise NotFoundError(
        f"Dokumentet {sha256[:12]}… är inte indexerat och kan inte läsas ännu. "
        "Sök i de indexerade dokumenten med search_documents."
    )


def find_section(
    session: Session,
    visibility: Visibility,
    sha256: str,
    section_number: str | None,
    section_position: int | None,
) -> models.DocumentSection:
    """The visible section at this position, or else with this number.

    Raises `NotFoundError` when the file or the section is missing or not
    shown, `AmbiguousError` when several shown sections have the number or
    the number is not that of the section at the position, and
    `MissingArgumentError` when neither is given.
    """
    require_file(session, visibility, sha256)
    document = f"dokumentet {sha256[:12]}…"
    number = None if section_number is None else section_number.strip().rstrip(".")
    if section_position is not None:
        found = session.get(models.DocumentSection, (sha256, section_position))
        if found is None:
            raise NotFoundError(
                f"Det finns inget avsnitt på plats {section_position} i {document}. "
                "Se avsnitten och deras platser med get_outline."
            )
        if not visibility.shows_section(sha256, section_position):
            raise NotFoundError(_held_back(f"på plats {section_position}", document))
        if number is not None and found.number != number:
            its_number = f"nummer {found.number}" if found.number else "inget nummer"
            raise AmbiguousError(
                f"section_number {number} och section_position {section_position} är olika "
                f"avsnitt i {document}: plats {section_position} ({found.title}) har "
                f"{its_number}. Ange bara ett av dem, och kontrollera med get_outline."
            )
        return found
    if number is None:
        raise _no_section()

    table = models.DocumentSection
    rows = session.scalars(
        select(table).where(table.sha256 == sha256, table.number == number).order_by(table.position)
    ).all()
    if not rows:
        raise NotFoundError(
            f"Det finns inget avsnitt med nummer {number} i {document}. "
            "Kontrollera numret med get_outline."
        )
    shown = [row for row in rows if visibility.shows_section(sha256, row.position)]
    if not shown:
        raise NotFoundError(_held_back(number, document))
    if len(shown) > 1:
        listed = ", ".join(f"plats {row.position} ({row.title})" for row in shown)
        raise AmbiguousError(
            f"Numret {number} finns på flera avsnitt i {document}: {listed}. "
            "Ange section_position för det avsnitt du menar."
        )
    return shown[0]


def _held_back(section: str, document: str) -> str:
    return (
        f"Avsnitt {section} i {document} hålls tillbaka i granskningen och kan inte läsas. "
        "Svara utan det, eller sök efter samma innehåll med search_documents."
    )
