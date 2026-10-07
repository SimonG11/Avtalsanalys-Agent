"""Tests for avtalsagent.domain.dates: Swedish holidays, working days and month arithmetic.

The holidays are checked against the almanac for 2026 and 2027, Easter
against the published dates for 2024-2032, and the working-day counts
against the pilot's terms ("tio (10) Arbetsdagar innan", "inom 30
Arbetsdagar") on dates that cross Easter, midsummer and Christmas.
"""

from datetime import date

import pytest

from avtalsagent.domain import dates


@pytest.mark.parametrize(
    ("year", "easter"),
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2028, date(2028, 4, 16)),
        (2029, date(2029, 4, 1)),
        (2030, date(2030, 4, 21)),
        (2031, date(2031, 4, 13)),
        (2032, date(2032, 3, 28)),
    ],
)
def test_easter_sunday(year: int, easter: date) -> None:
    assert dates.easter_sunday(year) == easter


def test_the_public_holidays_of_2027_as_the_almanac_has_them() -> None:
    assert dates.public_holidays(2027) == {
        date(2027, 1, 1): "nyårsdagen",
        date(2027, 1, 6): "trettondedag jul",
        date(2027, 3, 26): "långfredagen",
        date(2027, 3, 28): "påskdagen",
        date(2027, 3, 29): "annandag påsk",
        date(2027, 5, 1): "första maj",
        date(2027, 5, 6): "Kristi himmelsfärdsdag",
        date(2027, 5, 16): "pingstdagen",
        date(2027, 6, 6): "Sveriges nationaldag",
        date(2027, 6, 26): "midsommardagen",
        date(2027, 11, 6): "alla helgons dag",
        date(2027, 12, 25): "juldagen",
        date(2027, 12, 26): "annandag jul",
    }


@pytest.mark.parametrize(
    ("year", "midsummer", "all_saints"),
    [(2026, date(2026, 6, 20), date(2026, 10, 31)), (2028, date(2028, 6, 24), date(2028, 11, 4))],
)
def test_midsummer_and_all_saints_are_saturdays_in_their_week(
    year: int, midsummer: date, all_saints: date
) -> None:
    holidays = dates.public_holidays(year)

    assert holidays[midsummer] == "midsommardagen"
    assert holidays[all_saints] == "alla helgons dag"
    assert midsummer.weekday() == all_saints.weekday() == 5


def test_the_eves_are_no_holidays() -> None:
    eves = dates.eves(2026)

    assert eves == {
        date(2026, 6, 19): "midsommarafton",
        date(2026, 12, 24): "julafton",
        date(2026, 12, 31): "nyårsafton",
    }
    assert all(dates.is_working_day(day) for day in eves)
    assert not any(dates.is_working_day(day, eves_off=True) for day in eves)


@pytest.mark.parametrize(
    ("day", "working"),
    [
        (date(2026, 10, 7), True),  # a Wednesday
        (date(2026, 10, 10), False),  # a Saturday
        (date(2026, 10, 11), False),  # a Sunday
        (date(2026, 12, 25), False),  # juldagen, a Friday
        (date(2027, 5, 6), False),  # Kristi himmelsfärdsdag, a Thursday
        (date(2027, 5, 7), True),  # the Friday after it
    ],
)
def test_a_working_day_is_a_weekday_that_is_no_holiday(day: date, working: bool) -> None:
    assert dates.is_working_day(day) is working


@pytest.mark.parametrize(
    ("start", "count", "end"),
    [
        (date(2026, 10, 7), 15, date(2026, 10, 28)),
        (date(2026, 10, 7), 90, date(2027, 2, 15)),  # past 12-25, 01-01 and 01-06
        (date(2027, 3, 22), 10, date(2027, 4, 7)),  # past långfredagen and annandag påsk
        (date(2027, 2, 17), -10, date(2027, 2, 3)),  # "tio (10) Arbetsdagar innan"
        (date(2026, 12, 28), -10, date(2026, 12, 11)),  # past juldagen; julafton counts
        (date(2026, 10, 9), 1, date(2026, 10, 12)),  # Friday to Monday
        (date(2026, 10, 10), 1, date(2026, 10, 12)),  # from a Saturday: the start is not counted
    ],
)
def test_working_days_are_counted_without_the_start_day(start: date, count: int, end: date) -> None:
    assert dates.add_working_days(start, count) == end


def test_with_the_eves_off_julafton_is_not_counted() -> None:
    assert dates.add_working_days(date(2026, 12, 28), -10, eves_off=True) == date(2026, 12, 10)


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (date(2027, 2, 17), -3, date(2026, 11, 17)),
        (date(2024, 11, 14), 48, date(2028, 11, 14)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),  # the month's last day
        (date(2024, 1, 31), 1, date(2024, 2, 29)),  # a leap year
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2028, 2, 29), -12, date(2027, 2, 28)),
    ],
)
def test_a_month_keeps_the_day_or_takes_the_months_last(
    start: date, months: int, end: date
) -> None:
    assert dates.add_months(start, months) == end


@pytest.mark.parametrize(
    ("unit", "end"),
    [
        ("days", date(2026, 10, 21)),
        ("working_days", date(2026, 10, 27)),  # alla helgons dag is a Saturday in 2026
        ("weeks", date(2027, 1, 13)),
        ("months", date(2027, 12, 7)),
        ("years", date(2040, 10, 7)),
    ],
)
def test_shift_moves_by_each_unit(unit: dates.Unit, end: date) -> None:
    assert dates.shift(date(2026, 10, 7), 14, unit) == end
