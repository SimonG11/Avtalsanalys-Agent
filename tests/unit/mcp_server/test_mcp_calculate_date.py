"""Tests for the tool calculate_date: the result, the step it writes and its notes.

The tool reads no database, so it is called directly and through the MCP
SDK's in-memory client, where the server must not open a session. The
last tests feed each step the tool writes to the register rule
(`validation/register_facts.py`), which must accept it, and reject it
with its result a day off: the rule redoes the step with the same
functions instead of trusting the tool's result.
"""

from datetime import date, timedelta
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from sqlalchemy.orm import Session

from avtalsagent.agent.schemas import FinalAnswer
from avtalsagent.domain import dates
from avtalsagent.mcp_server.errors import ArgumentError
from avtalsagent.mcp_server.server import build_server
from avtalsagent.mcp_server.tools.calculate_date import DateResult, Holiday, calculate_date
from avtalsagent.validation.register_facts import check_register_facts

HOSTS = ["localhost:*"]
TODAY = date(2026, 10, 7)


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


@pytest.mark.parametrize(
    ("arguments", "result", "step"),
    [
        (
            (date(2027, 2, 17), 3, "months", "before"),
            date(2026, 11, 17),
            "2027-02-17 minus 3 månader = 2026-11-17",
        ),
        (
            (date(2026, 10, 7), 15, "working_days", "after"),
            date(2026, 10, 28),
            "2026-10-07 plus 15 arbetsdagar = 2026-10-28",
        ),
        (
            (date(2026, 10, 7), 1, "days", "after"),
            date(2026, 10, 8),
            "2026-10-07 plus 1 dag = 2026-10-08",
        ),
        (
            (date(2026, 10, 7), 2, "weeks", "before"),
            date(2026, 9, 23),
            "2026-10-07 minus 2 veckor = 2026-09-23",
        ),
        (
            (date(2026, 10, 7), 1, "years", "after"),
            date(2027, 10, 7),
            "2026-10-07 plus 1 år = 2027-10-07",
        ),
    ],
    ids=["months before", "working days after", "one day", "weeks before", "a year"],
)
def test_the_result_and_the_step_that_gives_it(
    arguments: tuple[Any, ...], result: date, step: str
) -> None:
    answer = calculate_date(*arguments)

    assert (answer.result, answer.step) == (result, step)


@pytest.mark.parametrize(
    ("direction", "result", "step"),
    [
        (
            "after",
            date(2028, 11, 13),
            "2024-11-14 plus 48 månader = 2028-11-14, minus 1 dag = 2028-11-13",
        ),
        (
            "before",
            date(2020, 11, 15),
            "2024-11-14 minus 48 månader = 2020-11-14, plus 1 dag = 2020-11-15",
        ),
    ],
)
def test_with_the_start_day_in_the_period_the_result_moves_a_day_back_to_it(
    direction: str, result: date, step: str
) -> None:
    # As the register's periods: 48 months from 2024-11-14 to 2028-11-13.
    answer = calculate_date(date(2024, 11, 14), 48, "months", direction, include_start=True)  # type: ignore[arg-type]

    assert (answer.result, answer.step) == (result, step)


def test_the_start_day_in_a_count_of_working_days_is_an_error() -> None:
    with pytest.raises(ArgumentError, match="include_start gäller perioder"):
        calculate_date(date(2026, 10, 7), 10, "working_days", "after", include_start=True)


@pytest.mark.parametrize(
    ("arguments"),
    [
        (date(2006, 1, 1), 2, "years", "before"),
        (date(2005, 6, 1), 3650, "years", "before"),  # before year 1
        (date(2100, 12, 1), 1, "months", "after"),
        (date(2100, 1, 1), 3650, "years", "after"),
    ],
    ids=["before 2005", "before year 1", "after 2100", "far after"],
)
def test_a_result_outside_the_holidays_years_is_an_error(arguments: tuple[Any, ...]) -> None:
    with pytest.raises(ArgumentError, match="utanför 2005-01-01 till 2100-12-31"):
        calculate_date(*arguments)


def test_the_holidays_a_count_of_working_days_passes_are_listed() -> None:
    answer = calculate_date(date(2026, 10, 7), 90, "working_days", "after")

    assert answer == DateResult(
        result=date(2027, 2, 15),
        weekday="måndag",
        step="2026-10-07 plus 90 arbetsdagar = 2027-02-15",
        skipped=[
            Holiday(day=date(2026, 12, 25), name="juldagen"),
            Holiday(day=date(2027, 1, 1), name="nyårsdagen"),
            Holiday(day=date(2027, 1, 6), name="trettondedag jul"),
        ],
        notes=[
            "Julafton 2026-12-24 och nyårsafton 2026-12-31 räknas som arbetsdagar, eftersom "
            "aftnarna inte är allmänna helgdagar. Räknas de som helgdagar blir datumet 2027-02-17."
        ],
    )


def test_an_eve_that_would_change_the_date_gets_a_note_with_the_other_date() -> None:
    answer = calculate_date(date(2026, 12, 28), 10, "working_days", "before")

    assert answer.result == date(2026, 12, 11)
    assert answer.skipped == [Holiday(day=date(2026, 12, 25), name="juldagen")]
    assert answer.notes == [
        "Julafton 2026-12-24 räknas som arbetsdag, eftersom aftnarna inte är allmänna "
        "helgdagar. Räknas den som helgdag blir datumet 2026-12-10."
    ]


@pytest.mark.parametrize(
    ("arguments", "result", "notes"),
    [
        (
            (date(2026, 12, 23), 4, "after"),
            date(2026, 12, 30),
            [
                "Julafton 2026-12-24 och nyårsafton 2026-12-31 räknas som arbetsdagar, eftersom "
                "aftnarna inte är allmänna helgdagar. Räknas de som helgdagar blir datumet "
                "2027-01-04."
            ],
        ),
        (
            (date(2027, 1, 4), 4, "before"),
            date(2026, 12, 28),
            [
                "Julafton 2026-12-24 och nyårsafton 2026-12-31 räknas som arbetsdagar, eftersom "
                "aftnarna inte är allmänna helgdagar. Räknas de som helgdagar blir datumet "
                "2026-12-23."
            ],
        ),
        (
            (date(2027, 6, 28), 1, "before"),
            date(2027, 6, 25),
            [
                "Midsommarafton 2027-06-25 räknas som arbetsdag, eftersom aftnarna inte är "
                "allmänna helgdagar. Räknas den som helgdag blir datumet 2027-06-24."
            ],
        ),
    ],
    ids=["an eve after the result", "an eve before it", "midsommarafton"],
)
def test_the_note_names_every_eve_the_other_date_depends_on(
    arguments: tuple[date, int, Any], result: date, notes: list[str]
) -> None:
    start, amount, direction = arguments
    answer = calculate_date(start, amount, "working_days", direction)

    assert (answer.result, answer.notes) == (result, notes)


def test_a_count_of_working_days_without_an_eve_has_no_note() -> None:
    answer = calculate_date(date(2027, 3, 22), 10, "working_days", "after")

    assert answer.result == date(2027, 4, 7)  # 2027-04-01 by the calendar
    assert [holiday.name for holiday in answer.skipped] == ["långfredagen", "annandag påsk"]
    assert answer.notes == []


@pytest.mark.parametrize(
    ("arguments", "notes"),
    [
        (
            (date(2026, 1, 31), 1, "months", "after"),
            [
                "Februari 2026 har ingen dag 31: månadens sista dag används.",
                "2026-02-28 är en lördag. Datumet flyttas inte; säger avtalet vad som gäller då, "
                "följ det.",
            ],
        ),
        (
            (date(2026, 12, 11), 14, "days", "after"),
            [
                "2026-12-25 är juldagen, en allmän helgdag. Datumet flyttas inte; säger avtalet "
                "vad som gäller då, följ det."
            ],
        ),
        ((date(2026, 10, 7), 14, "days", "after"), []),
    ],
    ids=["a short month on a Saturday", "a holiday", "a weekday"],
)
def test_a_calendar_result_is_not_moved_but_noted(
    arguments: tuple[Any, ...], notes: list[str]
) -> None:
    answer = calculate_date(*arguments)

    assert answer.notes == notes
    assert answer.skipped == []


# --- through MCP ---


@pytest.mark.anyio
async def test_through_mcp_the_tool_opens_no_session() -> None:
    sessions = NoSessions()
    server = build_server(sessions, None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(
            "calculate_date",
            {"start": "2027-02-17", "amount": 3, "unit": "months", "direction": "before"},
        )

    assert result.isError is False
    assert result.structuredContent == {
        "result": "2026-11-17",
        "weekday": "tisdag",
        "step": "2027-02-17 minus 3 månader = 2026-11-17",
        "skipped": [],
        "notes": [],
    }
    assert sessions.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("arguments", "argument"),
    [
        ({"start": "1899-12-31", "amount": 3, "unit": "months", "direction": "after"}, "start"),
        ({"start": "2004-12-31", "amount": 3, "unit": "months", "direction": "after"}, "start"),
        ({"start": "2027-02-31", "amount": 3, "unit": "months", "direction": "after"}, "start"),
        ({"start": "2027-02-17", "amount": 0, "unit": "months", "direction": "after"}, "amount"),
        ({"start": "2027-02-17", "amount": 3651, "unit": "days", "direction": "after"}, "amount"),
        ({"start": "2027-02-17", "amount": 3, "unit": "kvartal", "direction": "after"}, "unit"),
        ({"start": "2027-02-17", "amount": 3, "unit": "days", "direction": "efter"}, "direction"),
        ({"start": "2027-02-17", "amount": 3, "unit": "days"}, "direction"),
    ],
    ids=[
        "too early",
        "before the holidays",
        "no such day",
        "zero",
        "too many",
        "a unit",
        "a direction",
        "no direction",
    ],
)
async def test_an_argument_out_of_range_is_an_error_the_model_reads(
    arguments: dict[str, Any], argument: str
) -> None:
    server = build_server(NoSessions(), None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("calculate_date", arguments)

    assert result.isError is True
    assert isinstance(result.content[0], TextContent)
    assert argument in result.content[0].text


@pytest.mark.anyio
async def test_the_units_and_directions_are_listed_in_the_schema() -> None:
    server = build_server(NoSessions(), None, allowed_hosts=HOSTS)

    async with create_connected_server_and_client_session(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    schema = tools["calculate_date"].inputSchema
    assert schema["properties"]["unit"]["enum"] == [
        "days",
        "working_days",
        "weeks",
        "months",
        "years",
    ]
    assert schema["properties"]["direction"]["enum"] == ["after", "before"]
    assert set(schema["required"]) == {"start", "amount", "unit", "direction"}


# --- the register rule redoes the tool's step ---


@pytest.mark.parametrize("unit", ["days", "working_days", "weeks", "months", "years"])
@pytest.mark.parametrize("direction", ["after", "before"])
@pytest.mark.parametrize("amount", [1, 10, 30, 90])
def test_the_register_rule_redoes_every_step_the_tool_writes(
    unit: Any, direction: Any, amount: int
) -> None:
    sign = 1 if direction == "after" else -1
    periods = [False] if unit == "working_days" else [False, True]
    for include_start in periods:
        try:
            answer = calculate_date(TODAY, amount, unit, direction, include_start=include_start)
        except ArgumentError:
            assert not dates.FIRST_DAY <= dates.shift(TODAY, sign * amount, unit) <= dates.LAST_DAY
            continue
        result = answer.result.isoformat()
        # Off by a day: towards the start in working days, since the eves' date is further away.
        off = (
            answer.result - timedelta(days=sign if unit == "working_days" else -sign)
        ).isoformat()
        stem, _ = answer.step.rsplit(f"= {result}", 1)

        right = check_register_facts(answer_text(f"{stem}= {result}"), {}, [], [], TODAY)
        wrong = check_register_facts(answer_text(f"{stem}= {off}"), {}, [], [], TODAY)

        assert right.problems == [], answer.step
        assert any(f"stämmer inte: det blir {result}" in problem for problem in wrong.problems), (
            answer.step
        )


def test_the_register_rule_backs_the_date_the_eve_note_gives() -> None:
    answer = calculate_date(date(2027, 1, 11), 10, "working_days", "before")
    other = answer.notes[0].removesuffix(".").rsplit(" ", 1)[1]
    text = (
        f"Avbeställ senast {answer.step}. Räknas julafton och nyårsafton som helgdagar blir det "
        f"{other}."
    )

    report = check_register_facts(
        answer_text(text), {}, [], ["Uppdraget startar 2027-01-11."], TODAY
    )

    assert other == "2026-12-21"
    assert report.problems == []


def answer_text(text: str) -> FinalAnswer:
    return FinalAnswer(answered=True, text=f"Från i dag räknat: {text}.", register_facts=[])
