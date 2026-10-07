"""What the tools may show: the files and sections the search index holds.

What:
    `Visibility` says whether a file, or a section of it, may be shown;
    `load_visibility` reads it from the database. `register_agreements`,
    `register_procurements` and `register_area` find an agreement number, a
    procurement number or a framework area in the register and return the
    register's spellings; `document_filters` turns the document tools'
    filters into the search's, in the spelling the index stored, and
    `require_loaded` refuses an area, agreement or procurement whose
    documents are not loaded.

Why:
    The tools must fail closed, as the index does (ADR 0011): a file or
    section that steps 4 and 5 hold back must not reach the agent by another
    way than the search, such as reading it by its hash or following a
    reference to it. Using the same rule as step 6 means one rule to explain:
    a file is shown when it has a row in `document_scope`, which step 6 writes
    only for files with indexed chunks, and a section when, in addition, the
    quarantine does not hold it. After `process` and before `index` the
    tools show nothing. The register check turns a misspelt or unknown
    filter into an error the model can act on, instead of an empty result
    it would take as "the agreements say nothing". The register has every
    framework area, but documents are loaded only for the pilot's, so a
    filter the register knows can still match no file. That is an error
    too, which names the areas that are loaded and tells the model to
    answer from the register: an empty result would read as "framgår
    inte", or lead the model to search without the filter and cite another
    area's terms. Both document tools get their filters from
    `document_filters`, so they refuse the same filters.

How:
    The indexed files are read from `document_scope` and the quarantine from
    the stored findings (`extraction_store.quarantine`), once per tool call.
    Numbers are compared by `agreement_key` and `procurement_key`
    (domain/identifiers.py), so "23.3.5890-23-003" finds
    "23.3-5890-2023-003"; an area by case-insensitive equality. The register
    can write one agreement two ways on different rows
    ("23.3-12000-2020-001" and "23.3-12000-2020-01"), so an agreement number
    finds every spelling with its key. Step 6 stores one of them in the
    scopes, and the document filter uses that one.
    Whether a filter's documents are loaded is one query on
    `document_scope`, with the search's own condition
    (`hybrid_search.scope_conditions`) for the area and for the agreement
    or procurement, each alone and without the document type: a type
    without documents is a plain empty answer. A file counts when the
    visibility shows it, so one the quarantine holds back whole does not,
    as in the tools' answers. When no file is shown at all (no index), the
    check passes, and the tools say that the index is missing.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, String, and_, any_, bindparam, select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.extracted import DocumentType, Quarantine
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.domain.search import SearchFilters
from avtalsagent.ingestion.extraction_store import quarantine
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.retrieval.hybrid_search import scope_conditions


@dataclass(frozen=True)
class Visibility:
    """The indexed files and the quarantine: what a tool may show."""

    files: frozenset[str]
    quarantine: Quarantine

    def shows_file(self, sha256: str) -> bool:
        return sha256 in self.files and not self.quarantine.holds(sha256)

    def shows_section(self, sha256: str, position: int) -> bool:
        return self.shows_file(sha256) and not self.quarantine.holds(sha256, position)


def load_visibility(session: Session) -> Visibility:
    """What the tools may show now, from the index and the stored findings."""
    files = frozenset(session.scalars(select(models.DocumentScope.sha256)))
    return Visibility(files=files, quarantine=quarantine(session))


def register_agreements(session: Session, agreement_number: str) -> list[str]:
    """The register's spellings of the agreement, sorted; empty when it has no such agreement.

    Every agreement number with the same `agreement_key` is one, the number
    itself included when the register has it.
    """
    key = agreement_key(agreement_number)
    return sorted(
        number
        for number in session.scalars(select(models.Agreement.agreement_number))
        if number == agreement_number or (key is not None and agreement_key(number) == key)
    )


def _indexed_spelling(session: Session, spellings: list[str]) -> str:
    """The one of the agreement's spellings that the index stored; the first when it has none.

    Step 6 keeps one spelling per agreement key in `document_scope`, and only
    that one finds the agreement's card there.
    """
    if len(spellings) == 1:
        return spellings[0]
    scope = models.DocumentScope
    for spelling in spellings:
        number = bindparam("agreement_number", spelling, String())
        stored = select(scope.sha256).where(number == any_(scope.agreement_numbers)).limit(1)
        if session.scalar(stored) is not None:
            return spelling
    return spellings[0]


def register_procurements(session: Session, number: str) -> list[str]:
    """The register's procurement numbers with the same key as `number`, sorted; empty for none.

    A number with a supplier's sequence ("23.3-5890-2023-003") is no
    procurement number and finds none.
    """
    key = procurement_key(number)
    if key is None:
        return []
    procurements = session.scalars(select(models.Procurement.procurement_number))
    return sorted(
        procurement for procurement in procurements if procurement_key(procurement) == key
    )


def register_area(session: Session, framework_area: str) -> str:
    """The framework area as the register spells it; `NotFoundError` listing the known ones."""
    areas = sorted(set(session.scalars(select(models.SubArea.framework_area))))
    wanted = framework_area.strip().casefold()
    for area in areas:
        if area.casefold() == wanted:
            return area
    known = ", ".join(areas)
    raise NotFoundError(
        f"Ramavtalsområdet '{framework_area}' finns inte i registret. Områden: {known}."
        if areas
        else f"Ramavtalsområdet '{framework_area}' finns inte: registret är tomt."
    )


def document_filters(
    session: Session,
    visibility: Visibility,
    framework_area: str | None,
    agreement_number: str | None,
    document_type: DocumentType | None,
) -> SearchFilters:
    """The filters of `search_documents` and `list_documents`, in the register's spelling.

    An agreement number keeps the agreement's card and its procurement's
    shared files, in the spelling the index stored for it; a number the
    register has as a procurement, without a supplier's sequence
    ("23.3-5890-2023"), keeps every file of the procurement. A number or
    area the register does not have is a `NotFoundError`, and so is one
    whose documents are not loaded (`require_loaded`).
    """
    area = None if framework_area is None else register_area(session, framework_area)
    agreement: str | None = None
    procurement: str | None = None
    if agreement_number is not None:
        if spellings := register_agreements(session, agreement_number):
            agreement = _indexed_spelling(session, spellings)
        else:
            # The register writes a procurement one way (M1 takes it from the agreement
            # numbers), so there is one; sorted() makes any other case predictable.
            procurement = next(iter(register_procurements(session, agreement_number)), None)
            if procurement is None:
                raise NotFoundError(
                    f"Numret {agreement_number} finns inte i registret, varken som avtal eller "
                    "som upphandling. Sök avtalet med search_register, till exempel på "
                    "leverantörens namn."
                )
    filters = SearchFilters(
        framework_area=area,
        procurement_number=procurement,
        agreement_number=agreement,
        document_type=None if document_type is None else document_type.value,
    )
    require_loaded(session, visibility, filters)
    return filters


def require_loaded(session: Session, visibility: Visibility, filters: SearchFilters) -> None:
    """`NotFoundError` when no file the tools show matches the area, agreement or procurement.

    Each is matched alone, as the search's filter matches it, and the
    document type is left out. The message names the areas whose documents
    are loaded and tells the model to answer from the register. When no
    file is shown at all (no index), nothing is raised: the tools say that
    the index is missing.
    """
    wanted: list[tuple[str, str, SearchFilters]] = []  # what, whose documents, its filter alone
    if (area := filters.framework_area) is not None:
        wanted.append((f"ramavtalsområdet {area}", "områdets", SearchFilters(framework_area=area)))
    if (agreement := filters.agreement_number) is not None:
        alone = SearchFilters(agreement_number=agreement)
        wanted.append((f"avtalet {agreement}", "avtalets", alone))
    if (procurement := filters.procurement_number) is not None:
        alone = SearchFilters(procurement_number=procurement)
        wanted.append((f"upphandlingen {procurement}", "upphandlingens", alone))
    if not wanted:
        return
    scope = models.DocumentScope
    matches: list[ColumnElement[bool]] = [and_(*scope_conditions(f)) for _, _, f in wanted]
    rows = session.execute(select(scope.sha256, scope.framework_areas, *matches))
    shown = [(areas, found) for sha256, areas, *found in rows if visibility.shows_file(sha256)]
    if not shown:  # no index, as between `process` and `index`
        return
    missing = [
        (what, whose)
        for n, (what, whose, _) in enumerate(wanted)
        if not any(found[n] for _, found in shown)
    ]
    if not missing:
        return
    named = " och ".join(what for what, _ in missing)
    whose = missing[0][1] if len(missing) == 1 else "deras"
    loaded = ", ".join(sorted({name for areas, _ in shown for name in areas}))
    raise NotFoundError(
        f"{named[0].upper()}{named[1:]} finns i registret, men {whose} dokument är inte inlästa. "
        f"Områden med inlästa dokument: {loaded}. Svara med det registret säger "
        f"(search_register) och säg till användaren att {whose} dokument inte är inlästa, i "
        "stället för att svara att det inte framgår eller citera andra avtals dokument."
    )
