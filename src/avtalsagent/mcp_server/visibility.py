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
    it would take as "the agreements say nothing". An unknown area's error
    guesses the areas meant ("Bemanning" for "Bemanningstjänster") on its
    first line, which the user sees in the step, and lists every area on
    the next, for the model. The register has every
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
    "23.3-5890-2023-003"; an area by case-insensitive equality. The guessed
    areas are those that contain the unknown one, then those `difflib`
    finds alike, at most `GUESSES`. The register
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
    as in the tools' answers. When no file is shown at all (no index, or
    every file held back), there is no loaded area to name, so the check
    passes and the tools answer as before.
"""

from dataclasses import dataclass
from difflib import get_close_matches

from sqlalchemy import ColumnElement, String, and_, any_, bindparam, select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.extracted import DocumentType, Quarantine
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.domain.search import SearchFilters
from avtalsagent.ingestion.extraction_store import quarantine
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.retrieval.hybrid_search import scope_conditions

GUESSES = 3  # the most areas an unknown area's error suggests
_ALIKE = 0.75  # how alike in spelling (difflib's ratio) a guessed area or word must be


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
    """The framework area as the register spells it; `NotFoundError` listing the known ones.

    The error's first line says that the area is not in the register and
    guesses the areas meant; the next lists every area.
    """
    areas = sorted(set(session.scalars(select(models.SubArea.framework_area))))
    wanted = framework_area.strip().casefold()
    for area in areas:
        if area.casefold() == wanted:
            return area
    if not areas:
        raise NotFoundError(f"Ramavtalsområdet '{framework_area}' finns inte: registret är tomt.")
    guesses = _guessed_areas(wanted, areas)
    guess = f" Menade du {_either(guesses)}?" if guesses else ""
    raise NotFoundError(
        f"Ramavtalsområdet '{framework_area}' finns inte i registret.{guess}\n"
        f"Områden i registret: {', '.join(areas)}."
    )


def _guessed_areas(wanted: str, areas: list[str]) -> list[str]:
    """The areas `wanted` (casefolded) may mean, at most `GUESSES`; empty when none is alike.

    First the areas that contain it or that it contains ("IT-drift Större"),
    those that start alike first ("it" is IT-drift before E-litteratur);
    else those alike in spelling ("bemanningstjanster"); else those with a
    word alike ("programvara").
    """
    if not wanted:
        return []
    folded = {area.casefold(): area for area in areas}
    starts = sorted(
        (
            not (f.startswith(wanted) or wanted.startswith(f)),
            not any(word.startswith(wanted) for word in f.split()),
            area,
        )
        for f, area in folded.items()
        if wanted in f or f in wanted
    )
    found = [area for *_, area in starts]
    if not found:
        found = [folded[f] for f in get_close_matches(wanted, folded, GUESSES, _ALIKE)]
    if not found:
        words = {word: area for f, area in folded.items() for word in f.split()}
        found = [words[w] for w in get_close_matches(wanted, words, GUESSES, _ALIKE)]
    return list(dict.fromkeys(found))[:GUESSES]


def _either(names: list[str]) -> str:
    """'A', 'A eller B', 'A, B eller C'."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} eller {names[-1]}"


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
    """`NotFoundError` when the area, agreement or procurement, each alone, matches no shown file.

    Each is matched as the search's filter matches it, without the
    document type. The message's first line says whose documents are not
    loaded; the next names the areas whose documents are loaded and says
    what the model can do: answer from the register, or, when only the area
    is missing and the agreement's documents are loaded, search again
    without the area. When no file is shown at all (no index, or
    every file held back), nothing is raised and the tools answer as before.
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
    if not shown:  # no index (as between `process` and `index`), or every file held back
        return
    missing = [n for n in range(len(wanted)) if not any(found[n] for _, found in shown)]
    if not missing:
        return
    loaded = ", ".join(sorted({name for areas, _ in shown for name in areas}))
    if len(missing) == 1:
        what, whose, _ = wanted[missing[0]]
        named = _capitalised(what)
    else:  # the area and the agreement or procurement: at most two
        whose = "deras"
        named = f"Både {wanted[0][0]} och {wanted[1][0]}"
    said = f"{named} finns i registret, men {whose} dokument är inte inlästa.\n"
    if missing == [0] and len(wanted) == 2:  # only the area: the agreement's are loaded elsewhere
        other = wanted[1][0]
        raise NotFoundError(
            f"{said}{_capitalised(other)} har inlästa dokument, men inte i det området. "
            f"Sök igen utan framework_area, eller med området som search_register anger för "
            f"{other}."
        )
    raise NotFoundError(
        f"{said}Områden med inlästa dokument: {loaded}. Svara med det registret säger "
        f"(search_register) och säg till användaren att {whose} dokument inte är inlästa, i "
        "stället för att svara att det inte framgår eller citera andra avtals dokument."
    )


def _capitalised(text: str) -> str:
    return f"{text[0].upper()}{text[1:]}"
