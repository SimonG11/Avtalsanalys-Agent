"""Tests for the document tools' checks that come before any query.

`read_section` and `resolve_reference` take a section by its number, its
position or both; `search_documents` needs the embedding model. These
checks run before the tool reads the database, so the tests pass a session
that fails on any use. What the tools read is tested on Postgres in
tests/integration/test_mcp_documents.py.
"""

from typing import cast

import pytest
from sqlalchemy.orm import Session

from avtalsagent.mcp_server.errors import MissingArgumentError, UnavailableError
from avtalsagent.mcp_server.tools.read_section import check_section_arguments, read_section
from avtalsagent.mcp_server.tools.resolve_reference import resolve_reference
from avtalsagent.mcp_server.tools.search_documents import search_documents

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
