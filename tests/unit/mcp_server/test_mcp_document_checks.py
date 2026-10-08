"""Tests for the document tools' checks that come before the search or the list.

`read_section` and `resolve_reference` take a section by its number, its
position or both; `search_documents` needs the embedding model. These
checks run before the tool reads the database, so the tests pass a session
that fails on any use. `search_documents` and `list_documents` also refuse
an area, agreement or procurement that the register has but no shown file
matches (`visibility.require_loaded`), before the question is embedded;
those tests pass a stub session with the register, the indexed files and
which filter values each file matches, as the SQL would. What the tools
read, and which files a filter matches, is tested on Postgres in
tests/integration/test_mcp_documents.py.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Select
from sqlalchemy.orm import Session

from avtalsagent.domain.extracted import DocumentType, Quarantine
from avtalsagent.domain.search import SearchFilters
from avtalsagent.mcp_server.errors import MissingArgumentError, NotFoundError, UnavailableError
from avtalsagent.mcp_server.tools.list_documents import list_documents
from avtalsagent.mcp_server.tools.read_section import check_section_arguments, read_section
from avtalsagent.mcp_server.tools.resolve_reference import resolve_reference
from avtalsagent.mcp_server.tools.search_documents import search_documents
from avtalsagent.mcp_server.visibility import Visibility, document_filters, load_visibility

SHA = "a" * 64


class NoDatabase:
    """Stands in for a Session; any use of it fails the test."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the tool used the database (session.{name})")


NO_DATABASE = cast(Session, NoDatabase())


@pytest.mark.parametrize(
    ("number", "position"),
    [("6.21.9", None), (None, 0), ("6.21.9", 3)],
    ids=["number", "position 0, the first section", "both, as a search hit gives them"],
)
def test_a_section_is_named_by_its_number_its_position_or_both(
    number: str | None, position: int | None
) -> None:
    check_section_arguments(number, position)  # does not raise


def test_without_a_number_or_a_position_the_model_is_told_where_to_find_one() -> None:
    message = "Ange section_number eller section_position .* get_outline"
    with pytest.raises(MissingArgumentError, match=message):
        read_section(NO_DATABASE, SHA)
    with pytest.raises(MissingArgumentError, match=message):
        resolve_reference(NO_DATABASE, SHA, reference="punkt 6.21.9")


def test_the_search_without_an_embedding_model_says_what_to_use_instead() -> None:
    with pytest.raises(UnavailableError, match="OPENAI_API_KEY.* list_documents och get_outline"):
        search_documents(NO_DATABASE, None, "Hur stort är vitet?")


# --- a filter whose documents are not loaded ---

PILOT = (
    "Bemanningstjänster",
    "IT-drift",
    "IT-konsulttjänster Resurskonsulter",
    "Programvaror och tjänster",
)
FURNITURE_AREA = "Möbler och inredning"  # in the register, no files fetched
IT_DRIFT = "23.3-5890-2023"
ADVANIA = "23.3-5890-2023-003"
BEMANNING = "23.3-14537-2023"
A_HUB = "23.3-14537-2023-001"  # no card of its own: its documents are the procurement's
FURNITURE_PROCUREMENT = "23.3-10777-2024"  # invented, as the agreement below
FURNITURE = "23.3-10777-2024-002"
SHARED = "e" * 64  # Bemanningstjänster's only file, shared by its suppliers
INDEX_BUILD = SimpleNamespace(
    embedding_model="topics:6",
    analyser="sv-1",
    term_count=1,
    chunk_count=1,
    held_back_count=0,
    document_count=5,
    built_at=datetime(2026, 10, 6, tzinfo=UTC),
)


@dataclass(frozen=True)
class IndexedFile:
    """A row of document_scope: the file's areas and the filter values its scope matches."""

    sha256: str
    areas: tuple[str, ...]
    matches: frozenset[str]  # as `hybrid_search.scope_conditions` matches them on Postgres


FILES = (
    IndexedFile("a" * 64, ("IT-drift",), frozenset({"IT-drift", IT_DRIFT, ADVANIA})),  # terms
    IndexedFile("c" * 64, ("IT-drift",), frozenset({"IT-drift", IT_DRIFT, ADVANIA})),  # card
    IndexedFile(SHARED, (PILOT[0],), frozenset({PILOT[0], BEMANNING, A_HUB})),
    IndexedFile("1" * 64, (PILOT[2],), frozenset({PILOT[2]})),
    IndexedFile("2" * 64, (PILOT[3],), frozenset({PILOT[3]})),
)


def one_line(statement: Select[Any]) -> str:
    return " ".join(str(statement).split())


class Index:
    """Stands in for a Session: the register, the indexed files and the files held back whole.

    The check of loaded documents selects each file's sha256 and areas and
    one column per filter, the area's first, then the agreement's or the
    procurement's; the stub answers each column from the statement's
    parameters and the file's `matches`. `checks` keeps those statements.
    """

    def __init__(self, files: Sequence[IndexedFile] = FILES, held: Collection[str] = ()) -> None:
        self.files = files
        self.held = held
        self.checks: list[str] = []

    def scalars(self, statement: Select[Any]) -> list[str]:
        text = one_line(statement)
        if text.startswith("SELECT parsed_file.sha256"):
            return []  # steps 4 and 5 have checked every parsed file
        return {
            "SELECT sub_area.framework_area FROM sub_area": [*PILOT, FURNITURE_AREA],
            "SELECT agreement.agreement_number FROM agreement": [ADVANIA, A_HUB, FURNITURE],
            "SELECT procurement.procurement_number FROM procurement": [
                IT_DRIFT,
                BEMANNING,
                FURNITURE_PROCUREMENT,
            ],
            "SELECT document_scope.sha256 FROM document_scope": [f.sha256 for f in self.files],
        }[text]

    def execute(self, statement: Select[Any]) -> list[tuple[object, ...]]:
        text = one_line(statement)
        if "FROM validation_finding" in text:
            return [(sha256, None) for sha256 in self.held]
        assert text.startswith("SELECT document_scope.sha256, document_scope.framework_areas, ")
        self.checks.append(text)
        parameters = statement.compile().params
        values = [
            parameters[name]
            for name in ("framework_area", "agreement_number", "procurement_number")
            if name in parameters
        ]
        return [(f.sha256, list(f.areas), *(v in f.matches for v in values)) for f in self.files]

    def scalar(self, statement: Select[Any]) -> SimpleNamespace:
        assert "FROM index_build" in str(statement), statement
        return INDEX_BUILD


class NoEmbedder:
    """Stands in for the embedding model: the filters are checked before any embedding."""

    name = "topics:6"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("the tool embedded documents")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("the tool embedded the question")


def search(session: Session, **filters: Any) -> object:
    return search_documents(session, NoEmbedder(), "Hur stort är vitet?", **filters)


def listing(session: Session, **filters: Any) -> object:
    return list_documents(session, **filters)


TOOLS = pytest.mark.parametrize("tool", [search, listing], ids=["search", "list"])


def filters_of(index: Index, **filters: Any) -> SearchFilters:
    """`document_filters` as both tools call it, with the visibility the stub gives."""
    session = cast(Session, index)
    return document_filters(
        session,
        load_visibility(session),
        filters.get("framework_area"),
        filters.get("agreement_number"),
        filters.get("document_type"),
    )


@TOOLS
def test_an_area_outside_the_pilot_says_which_areas_are_loaded(tool: Any) -> None:
    index = Index()

    with pytest.raises(NotFoundError) as error:
        tool(cast(Session, index), framework_area="möbler och inredning")

    assert str(error.value) == (
        "Ramavtalsområdet Möbler och inredning finns i registret, men områdets dokument är inte "
        "inlästa. Områden med inlästa dokument: Bemanningstjänster, IT-drift, "
        "IT-konsulttjänster Resurskonsulter, Programvaror och tjänster. Svara med det registret "
        "säger (search_register) och säg till användaren att områdets dokument inte är inlästa, "
        "i stället för att svara att det inte framgår eller citera andra avtals dokument."
    )
    assert len(index.checks) == 1  # one query for the check


@TOOLS
@pytest.mark.parametrize(
    ("filters", "start"),
    [
        (
            {"agreement_number": FURNITURE},
            f"Avtalet {FURNITURE} finns i registret, men avtalets dokument är inte inlästa. "
            "Områden med inlästa dokument: Bemanningstjänster, IT-drift, ",
        ),
        (
            {"agreement_number": FURNITURE_PROCUREMENT},
            f"Upphandlingen {FURNITURE_PROCUREMENT} finns i registret, men upphandlingens "
            "dokument är inte inlästa.",
        ),
        # The area has documents, the agreement has none: the error is about the agreement.
        (
            {"framework_area": "IT-drift", "agreement_number": FURNITURE},
            f"Avtalet {FURNITURE} finns i registret, men avtalets dokument",
        ),
        (
            {"framework_area": FURNITURE_AREA, "agreement_number": FURNITURE},
            f"Både ramavtalsområdet {FURNITURE_AREA} och avtalet {FURNITURE} finns i registret, "
            "men deras dokument är inte inlästa.",
        ),
        # A document type does not turn it into an empty answer.
        (
            {"agreement_number": FURNITURE, "document_type": DocumentType.GENERAL_TERMS},
            f"Avtalet {FURNITURE} finns i registret, men avtalets dokument",
        ),
    ],
    ids=[
        "agreement",
        "procurement",
        "loaded area, agreement",
        "both",
        "agreement and type",
    ],
)
def test_an_agreement_or_procurement_outside_the_pilot_is_an_error(
    tool: Any, filters: dict[str, Any], start: str
) -> None:
    with pytest.raises(NotFoundError) as error:
        tool(cast(Session, Index()), **filters)

    assert str(error.value).startswith(start)
    assert "Svara med det registret säger (search_register)" in str(error.value)


@TOOLS
def test_an_area_outside_the_pilot_with_a_loaded_agreement_says_to_search_without_it(
    tool: Any,
) -> None:
    with pytest.raises(NotFoundError) as error:
        tool(cast(Session, Index()), framework_area=FURNITURE_AREA, agreement_number=ADVANIA)

    # The agreement's documents are loaded, in another area: the register is not the answer.
    assert str(error.value) == (
        f"Ramavtalsområdet {FURNITURE_AREA} finns i registret, men områdets dokument är inte "
        f"inlästa. Avtalet {ADVANIA} har inlästa dokument, men inte i det området. Sök igen "
        f"utan framework_area, eller med området som search_register anger för avtalet {ADVANIA}."
    )


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"framework_area": "it-drift"}, SearchFilters(framework_area="IT-drift")),
        # A Hub has no card: its procurement's shared files are loaded, so it is loaded.
        ({"agreement_number": A_HUB}, SearchFilters(agreement_number=A_HUB)),
        ({"agreement_number": BEMANNING}, SearchFilters(procurement_number=BEMANNING)),
        # Each is loaded alone; that no file has both is an empty answer, not an error.
        (
            {"framework_area": "Bemanningstjänster", "agreement_number": ADVANIA},
            SearchFilters(framework_area="Bemanningstjänster", agreement_number=ADVANIA),
        ),
        # So is a type that the agreement has no documents of.
        (
            {"agreement_number": A_HUB, "document_type": DocumentType.AMENDMENT},
            SearchFilters(agreement_number=A_HUB, document_type="amendment"),
        ),
    ],
    ids=["area", "agreement with shared files only", "procurement", "area and agreement", "type"],
)
def test_a_filter_inside_the_pilot_passes_as_before(
    filters: dict[str, Any], expected: SearchFilters
) -> None:
    index = Index()

    assert filters_of(index, **filters) == expected
    assert len(index.checks) == 1
    assert "document_type" not in index.checks[0]  # the type is never part of the check


def test_a_file_held_back_whole_does_not_count_as_loaded() -> None:
    # As the tools show it: Bemanningstjänster's only file is held back, so nothing is shown.
    index = Index(held=[SHARED])

    with pytest.raises(NotFoundError) as area:
        filters_of(index, framework_area="Bemanningstjänster")
    with pytest.raises(NotFoundError, match=f"^Avtalet {A_HUB} finns i registret, men avtalets"):
        filters_of(index, agreement_number=A_HUB)

    assert str(area.value).startswith(
        "Ramavtalsområdet Bemanningstjänster finns i registret, men områdets dokument är inte "
        "inlästa. Områden med inlästa dokument: IT-drift, IT-konsulttjänster Resurskonsulter, "
        "Programvaror och tjänster. "
    )


@pytest.mark.parametrize(
    "held",
    [None, [f.sha256 for f in FILES]],
    ids=["no index", "every file held back"],
)
def test_with_no_file_shown_no_filter_is_called_not_loaded(held: list[str] | None) -> None:
    # No index (as between `process` and `index`) or every file held back: there is no loaded
    # area to name, so the filters pass and the tools answer as before.
    index = Index(files=()) if held is None else Index(held=held)

    area = filters_of(index, framework_area=FURNITURE_AREA)
    agreement = filters_of(index, agreement_number=FURNITURE)

    assert area == SearchFilters(framework_area=FURNITURE_AREA)
    assert agreement == SearchFilters(agreement_number=FURNITURE)


def test_a_document_type_alone_is_not_checked() -> None:
    nothing = Visibility(files=frozenset(), quarantine=Quarantine(frozenset(), frozenset()))

    filters = document_filters(NO_DATABASE, nothing, None, None, DocumentType.AMENDMENT)

    assert filters == SearchFilters(document_type="amendment")
