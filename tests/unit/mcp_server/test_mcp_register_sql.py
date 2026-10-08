"""Tests for what the register lookups ask and build, on a stub session without a database.

The register can write one agreement two ways on different rows (in the
2026-10-05 list "23.3-12000-2020-001" and "23.3-12000-2020-01"), and step 6
stores one of them in the scopes. The stub answers the register's agreement
numbers and which spelling the scopes hold, and keeps the SQL of every other
statement, so the tests can read the filter and the page that
`search_register` asks for. The same lookups on Postgres are tested in
tests/integration/test_mcp_register.py.
"""

from collections.abc import Collection
from typing import Any, cast

import pytest
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from avtalsagent.domain.extracted import Quarantine
from avtalsagent.domain.search import SearchFilters
from avtalsagent.mcp_server.errors import NotFoundError
from avtalsagent.mcp_server.tools.search_register import search_register, sub_area_parts
from avtalsagent.mcp_server.visibility import Visibility, document_filters, register_agreements

TWO_WAYS = ["23.3-12000-2020-001", "23.3-12000-2020-01"]
ADVANIA = "23.3-5890-2023-003"
CARD = "c" * 64
# The stub's document_scope has no rows for the check of loaded documents (that check is
# tested in test_mcp_document_checks.py), so it passes, as with no index.
NOTHING_SHOWN = Visibility(files=frozenset(), quarantine=Quarantine(frozenset(), frozenset()))


def sql(statement: Select[Any]) -> str:
    """The statement as Postgres gets it, values inline, on one line."""
    compiled = statement.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
        compile_kwargs={"literal_binds": True},
    )
    return " ".join(str(compiled).split())


class Register:
    """Stands in for a Session: the register's agreements and the spellings the scopes hold.

    `search_register`'s count is always 0, so a sub-area filter is always
    looked up; `sub_areas` is the answer to that lookup, and `names` the
    sub-area names an error then lists.
    """

    def __init__(
        self,
        indexed: Collection[str] = (),
        *,
        sub_areas: bool = True,
        names: Collection[str] = (),
    ) -> None:
        self.indexed = set(indexed)
        self.sub_areas = sub_areas
        self.names = sorted(names)
        self.scope_lookups: list[str] = []  # the spellings looked for in document_scope
        self.statements: list[str] = []  # every other statement, as SQL

    def scalars(self, statement: Select[Any]) -> list[str]:
        if sql(statement).startswith("SELECT DISTINCT sub_area.name FROM sub_area"):
            self.statements.append(sql(statement))
            return self.names
        assert sql(statement) == "SELECT agreement.agreement_number FROM agreement"
        return [ADVANIA, *TWO_WAYS]

    def scalar(self, statement: Select[Any]) -> object:
        if "FROM document_scope" in sql(statement):
            spelling = statement.compile().params["agreement_number"]
            self.scope_lookups.append(spelling)
            return CARD if spelling in self.indexed else None
        self.statements.append(sql(statement))
        if sql(statement).startswith("SELECT sub_area.id FROM sub_area"):
            return 7 if self.sub_areas else None
        return 0  # search_register's count

    def execute(self, statement: Select[Any]) -> list[object]:
        self.statements.append(sql(statement))
        return []


def stub(register: Register) -> Session:
    return cast(Session, register)


@pytest.mark.parametrize("number", [*TWO_WAYS, "23.3.12000-20-001"])
def test_an_agreement_number_finds_every_spelling_the_register_has(number: str) -> None:
    assert register_agreements(stub(Register()), number) == TWO_WAYS


@pytest.mark.parametrize("number", ["23.3-9999-2023-001", "23.3-12000-2020"])
def test_another_agreement_or_a_procurement_finds_no_spelling(number: str) -> None:
    assert register_agreements(stub(Register()), number) == []


@pytest.mark.parametrize(
    ("indexed", "spelling"),
    [(["23.3-12000-2020-01"], "23.3-12000-2020-01"), ([], "23.3-12000-2020-001")],
    ids=["the one the index stored", "the first, when the index has neither"],
)
@pytest.mark.parametrize("number", TWO_WAYS)
def test_the_document_filter_takes_the_spelling_the_index_stored(
    number: str, indexed: list[str], spelling: str
) -> None:
    # Only the stored spelling finds the agreement's card in document_scope.
    register = Register(indexed)

    filters = document_filters(stub(register), NOTHING_SHOWN, None, number, None)

    assert filters == SearchFilters(agreement_number=spelling)


def test_an_agreement_with_one_spelling_is_not_looked_up_in_the_index() -> None:
    register = Register()

    filters = document_filters(stub(register), NOTHING_SHOWN, None, "23.3.5890-23-003", None)

    assert filters == SearchFilters(agreement_number=ADVANIA)
    assert register.scope_lookups == []


@pytest.mark.parametrize("number", TWO_WAYS)
def test_search_register_keeps_the_rows_of_every_spelling(number: str) -> None:
    register = Register()

    search_register(stub(register), agreement_number=number)

    count, rows = register.statements
    spellings = "agreement.agreement_number IN ('23.3-12000-2020-001', '23.3-12000-2020-01')"
    assert spellings in count
    assert spellings in rows


def test_the_offset_skips_rows_after_the_order_but_not_in_the_total() -> None:
    register = Register()

    search_register(stub(register), framework_area=None, supplier="Advania", limit=20, offset=40)

    count, rows = register.statements
    assert rows.endswith(
        "ORDER BY sub_area.framework_area, agreement.agreement_number, sub_area.path "
        "LIMIT 20 OFFSET 40"
    )
    assert "LIMIT" not in count
    assert "OFFSET" not in count


# --- the sub-area filter ---


@pytest.mark.parametrize(
    ("written", "parts"),
    [
        ("Övre Norrland", ["Övre Norrland"]),
        ("IT-tjänster / Övre Norrland", ["IT-tjänster", "Övre Norrland"]),
        ("  IT-tjänster/Övre   Norrland / ", ["IT-tjänster", "Övre Norrland"]),
        ("Stockholm-Arlanda/Arlandastad", ["Stockholm-Arlanda", "Arlandastad"]),
        (" / ", []),
    ],
)
def test_a_sub_area_is_read_as_parts(written: str, parts: list[str]) -> None:
    assert sub_area_parts(written) == parts


def test_each_part_of_a_sub_area_must_be_in_the_path_in_count_and_page() -> None:
    register = Register()

    search_register(stub(register), sub_area="IT-tjänster / Övre Norrland")

    count, lookup, rows = register.statements
    # icontains with autoescape: case-insensitive, and % or _ in the text is that character.
    # The driver's format doubles each %; Postgres gets '%'.
    for part in ("IT-tjänster", "Övre Norrland"):
        condition = f"sub_area.path ILIKE '%%' || '{part}' || '%%' ESCAPE '/'"
        assert condition in count
        assert condition in rows
        assert condition in lookup
    assert lookup.endswith("LIMIT 1")


def test_a_wildcard_in_a_sub_area_is_a_character() -> None:
    register = Register()

    search_register(stub(register), sub_area="100%_IT")

    count = register.statements[0]
    assert "'100/%%/_IT'" in count  # escaped with /, then doubled by the driver's format


def test_a_sub_area_no_path_has_lists_the_areas_sub_areas() -> None:
    register = Register(sub_areas=False, names=["Övre Norrland", "Stockholm"])

    with pytest.raises(NotFoundError) as error:
        search_register(stub(register), sub_area="Övre Norland", supplier="Randstad")

    assert str(error.value) == (
        "Inget delområde innehåller 'Övre Norland'. Ange framework_area, så listas områdets "
        "delområden, eller sök med en kortare del av namnet."
    )


def test_a_sub_area_that_exists_gives_an_empty_page_not_an_error() -> None:
    # Other filters can exclude every row: then there is simply no such agreement.
    register = Register(sub_areas=True)

    result = search_register(stub(register), sub_area="Övre Norrland", supplier="Advania")

    assert (result.rows, result.total) == ([], 0)
