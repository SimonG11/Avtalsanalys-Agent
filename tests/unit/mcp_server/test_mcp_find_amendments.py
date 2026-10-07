"""Tests for the tool find_amendments without a database: its pure parts and its schema.

`excerpt` cuts the text around a reference, `answer_date` dates an answer in
a questions log by its TendSign stamps, and `newest_first` orders the
amendments. The log texts are the pilot's. The schema is read through the MCP
SDK's in-memory client with a session factory that must not be called. What
the tool reads is tested on Postgres in tests/integration/test_mcp_amendments.py.
"""

import re
from datetime import date

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from sqlalchemy.orm import Session

from avtalsagent.mcp_server.references import TargetRef
from avtalsagent.mcp_server.results import SectionRef
from avtalsagent.mcp_server.server import build_server
from avtalsagent.mcp_server.tools.find_amendments import (
    EXCERPT_CHARS,
    Amendment,
    answer_date,
    excerpt,
    newest_first,
)

HOSTS = ["localhost:*"]
TERMS = "a" * 64

# 7a765d649e25 §9: a correction in Kammarkollegiet's answer, the day after the question.
CORRECTION = (
    "Publik fråga 9\n\nFrån:\n\nDold\n\nDatum:\n\nTill:\n\n2024-02-19 13:35\n\nAlla\n\nI avsnitt "
    "Inbjudan att lämna anbud under punkterna 1.1 och 1.4 anges att 8 st "
    "ramavtalsleverantörer kommer att tilldelas ramavtal men i avsnitt Prövning och "
    "utvärdering under punkt 3.2 framgår att 7 st anbud kommer att antas. Vänligen förtydliga "
    "om det är 7 eller 8 ramavtalsleverantörer som kommer att tilldelas ramavtal (under "
    "förutsättning att så många anbud uppfyller ställda krav).\n\nPublikt svar\n\nFrån:\n\n"
    "Statens inköpscentral vid Kammarkollegiet Datum:\n\nTill:\n\n2024-02-20 08:39\n\nAlla\n\n"
    'Rättelse. Texten som gäller är följande för punkt 3.2: "De åtta (8) anbuden med de högsta '
    'jämförelsesummorna är de ekonomiskt mest fördelaktiga anbuden".'
)
ANSWERED_AT = CORRECTION.index("punkt 3.2", CORRECTION.index("Publikt svar"))


class NoSessions:
    """A session factory that must not be called; it counts the calls it gets."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Session:
        self.calls += 1
        raise RuntimeError("the test opened a session")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- excerpt ---------------------------------------------------------------------------------


def test_a_short_section_is_the_whole_excerpt_on_one_line() -> None:
    text = (
        "1 Ändring av punkt 6.21.4\n\nPunkt 6.21.4 i Allmänna villkor ersätts med följande:\n\n"
        '"Vitets storlek uppgår till 30 000 SEK."'
    )
    start = text.index("Punkt 6.21.4")

    assert excerpt(text, start, start + len("Punkt 6.21.4")) == (
        "1 Ändring av punkt 6.21.4 Punkt 6.21.4 i Allmänna villkor ersätts med följande: "
        '"Vitets storlek uppgår till 30 000 SEK."'
    )


def test_the_text_before_the_window_is_left_out_with_a_mark() -> None:
    found = excerpt(CORRECTION, ANSWERED_AT, ANSWERED_AT + len("punkt 3.2"))

    # 150 characters before the reference, from the start of the next word; the rest of the
    # section is shorter than 250 characters, so it ends without a mark.
    assert found == (
        "…Publikt svar Från: Statens inköpscentral vid Kammarkollegiet Datum: Till: "
        "2024-02-20 08:39 Alla Rättelse. Texten som gäller är följande för punkt 3.2: "
        '"De åtta (8) anbuden med de högsta jämförelsesummorna är de ekonomiskt mest '
        'fördelaktiga anbuden".'
    )


def test_a_long_excerpt_is_cut_between_words_to_the_limit() -> None:
    start = CORRECTION.index("punkt 3.2")  # in the question, with much text on both sides

    found = excerpt(CORRECTION, start, start + len("punkt 3.2"))

    assert len(found) == EXCERPT_CHARS
    assert found.startswith("…anbud under punkterna 1.1 och 1.4")
    assert found.endswith(" Publikt svar Från: Statens inköpscentral…")
    assert "under punkt 3.2 framgår" in found
    # Every word is a whole word of the text.
    words = set(CORRECTION.split())
    assert all(word in words for word in found.strip("…").split())


def test_a_window_that_starts_and_ends_between_words_cuts_nothing_more() -> None:
    text = " ".join(f"ord{n:03d}" for n in range(200))  # words of 6 characters and a space
    start = text.index("ord100")

    found = excerpt(text, start, start + 6)

    assert len(found) <= EXCERPT_CHARS
    assert found.startswith("…ord")
    assert found.endswith("…")
    assert "ord100" in found
    assert all(re.fullmatch(r"ord\d{3}", word) for word in found.strip("…").split())


def test_blank_space_left_out_is_no_cut() -> None:
    text = " " * 200 + "enligt punkt 6.21.4." + "\n" * 300

    assert excerpt(text, 207, 219) == "enligt punkt 6.21.4."


# --- answer_date -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "day"),
    [
        (CORRECTION, date(2024, 2, 20)),  # the question 2024-02-19, the answer after it
        (
            # 20c753d88340 §2: both stamps after "Publikt svar", the question's first.
            "Publik fråga 2\n\nFrån:\n\nDold\n\nKommer denna upphandling vara gällande någon av "
            "nedan tjänsteområden?\n\nPublikt svar\n\nFrån:\n\nStatens inköpscentral vid "
            "Kammarkollegiet\n\nUpphandlingsföremålet beskrivs i 1.5.1.\n\nDatum:\n\nTill:\n\n"
            "Datum:\n\nTill:\n\n2022-03-02 09:12\n\nAlla\n\n2022-03-03 08:16\n\nAlla",
            date(2022, 3, 3),
        ),
        (
            # 39d8c1efe373 §15: both before it, the answer's first.
            "Publik fråga 15\n\nFrån:\n\nDold\n\n4.2.2 Utvärdering av bilaga Kvalitet i "
            "utförande av Konsulttjänst\n\nDatum:\n\nTill:\n\n2025-02-12 14:57\n\nAlla\n\n"
            "Datum:\n\nTill:\n\n2025-02-11 16:39\n\nAlla\n\nPublikt svar\n\nFrån:\n\nStatens "
            "inköpscentral vid Kammarkollegiet\n\nEn konsult kan uppfylla ett eller flera av de "
            "tre tilldelningskriterierna.",
            date(2025, 2, 12),
        ),
        (
            # 20c753d88340 §14: the log was printed later.
            "Publik fråga 14\n\nFrån:\n\nDold\n\nFår en anbudsgivare vara åberopat företag till "
            "en annan anbudsgivare?\n\nPublikt svar\n\nFrån:\n\nStatens inköpscentral vid "
            "Kammarkollegiet\n\nNej, se 4.1.1.\n\nDatum:\n\nTill:\n\nDatum:\n\nTill:\n\n"
            "2022-03-04 14:03\n\nAlla\n\n2022-03-08 07:56\n\nAlla\n\nUtskrivet: 2022-10-13 09:20",
            date(2022, 3, 8),
        ),
        ("Publikt svar\n\nAvtalsvillkoret kvarstår oförändrat.", None),
        ("Publikt svar\n\nDatum:\n\n2024-13-45 10:00\n\n2024-02-20 08:39", date(2024, 2, 20)),
    ],
    ids=["after the answer", "both after it", "both before it", "printed", "none", "no date"],
)
def test_an_answer_is_dated_by_the_latest_stamp_of_its_section(text: str, day: date | None) -> None:
    assert answer_date(text) == day


# --- newest_first ----------------------------------------------------------------------------


def amendment(
    file_title: str,
    sha256: str,
    position: int,
    dated: date | None,
    amended_position: int | None = 1,
) -> Amendment:
    amending = SectionRef(
        sha256=sha256,
        file_title=file_title,
        document_type="amendment",
        page_titles=["IT-drift Mindre, upp till 200 anställda"],
        section_position=position,
        section_number=str(position + 1),
        section_title="Ändring",
        page_start=1,
        page_end=1,
    )
    amended = TargetRef(
        sha256=TERMS,
        file_title="Allmänna villkor",
        document_type="general_terms",
        page_titles=["IT-drift Mindre, upp till 200 anställda"],
        section_position=amended_position,
        section_number=None if amended_position is None else "6.21.4",
        section_title=None if amended_position is None else "Ansvar och vite",
        page_start=None if amended_position is None else 24,
        page_end=None if amended_position is None else 24,
    )
    return Amendment(
        amending=amending,
        amended=amended,
        raw="punkt 6.21.4",
        status="resolved",
        dated=dated,
        excerpt="Punkt 6.21.4 ersätts",
    )


def test_the_newest_come_first_and_the_undated_last() -> None:
    undated = amendment("Tillägg 2", "2" * 64, 0, None)
    older = amendment("Frågor och svar", "3" * 64, 6, date(2024, 2, 20))
    newer = amendment("Tillägg 1", "1" * 64, 0, date(2025, 1, 15))

    assert newest_first([undated, older, newer]) == [newer, older, undated]


def test_on_the_same_day_the_order_is_by_file_title_then_by_place() -> None:
    day = date(2025, 1, 15)
    second = amendment("Tillägg 1", "1" * 64, 1, day)
    first = amendment("Tillägg 1", "1" * 64, 0, day)
    whole_file = amendment("Tillägg 1", "1" * 64, 0, day, amended_position=None)
    other_title = amendment("Ett tillägg", "9" * 64, 3, day)

    assert newest_first([whole_file, second, other_title, first]) == [
        other_title,
        first,
        whole_file,  # the section it changes first, then the whole file
        second,
    ]


# --- through MCP -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_schema_takes_a_file_and_perhaps_a_section() -> None:
    sessions = NoSessions()
    server = build_server(sessions, None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["find_amendments"]
    assert tool.inputSchema["required"] == ["sha256"]
    assert list(tool.inputSchema["properties"]) == ["sha256", "section_number", "section_position"]
    assert tool.inputSchema["additionalProperties"] is False
    assert tool.description is not None
    assert tool.description.startswith("Hitta ändringar av ett avsnitt eller ett dokument.")
    for words in ("ändringsdokument", "Frågor och svar", "ambiguous", "read_section"):
        assert words in tool.description
    assert tool.outputSchema is not None
    assert set(tool.outputSchema["properties"]) == {"target", "amendments", "held_back"}
    assert sessions.calls == 0


@pytest.mark.anyio
async def test_a_malformed_hash_is_refused_before_any_session() -> None:
    sessions = NoSessions()
    server = build_server(sessions, None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("find_amendments", {"sha256": "abc"})

    assert result.isError is True
    assert isinstance(result.content[0], TextContent)
    assert "sha256" in result.content[0].text
    assert sessions.calls == 0
