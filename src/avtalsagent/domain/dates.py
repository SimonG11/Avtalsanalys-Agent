"""Swedish calendar arithmetic: days, working days, weeks, months and years.

What:
    `shift(day, amount, unit)` moves a date by a signed amount of one unit.
    `add_months` keeps the day of the month, or takes the month's last day
    when the month is shorter (2026-01-31 plus one month is 2026-02-28).
    `add_working_days` counts working days: Monday to Friday except the
    public holidays of `public_holidays(year)`, the "allmänna helgdagar" of
    Lag (1989:253), as they are since 2005 (`FIRST_DAY`). `eves(year)` are
    midsommarafton, julafton and nyårsafton, which the law does not make
    holidays. `WEEKDAYS` are the Swedish names of the days of the week.

Why:
    A model counts dates badly: thirty working days across Christmas, or
    a month from the 31st, are easy to get a day wrong. The tool
    `calculate_date` and the register rule (`validation/register_facts.py`)
    both use this module, so the step the tool writes is the step the rule
    redoes, and the rule needs no trust in a tool result from the message
    history. Pure functions, with no database, network or model, so every
    rule is tested on its own.

How:
    Easter by the anonymous Gregorian algorithm (Meeus/Jones/Butcher); the
    movable holidays follow from it (långfredagen two days before,
    Kristi himmelsfärdsdag 39 days after, pingstdagen 49 days after).
    Midsommardagen is the Saturday from 20 to 26 June and alla helgons dag
    the Saturday from 31 October to 6 November. Working days are stepped
    one day at a time, and the start day is never counted: one working day
    after a Friday is the Monday. Weeks are seven days and years twelve
    months.
"""

import calendar
from datetime import date, timedelta
from functools import cache
from typing import Literal

Unit = Literal["days", "working_days", "weeks", "months", "years"]

# The years these holidays hold for: since 2005 Sveriges nationaldag is a holiday and annandag
# pingst is not (SFS 2004:1072). The last is a bound for typing errors, not a law.
FIRST_DAY = date(2005, 1, 1)
LAST_DAY = date(2100, 12, 31)

WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")

_SATURDAY = 5
_MONTHS_PER_YEAR = 12
_DAYS_PER_WEEK = 7


def easter_sunday(year: int) -> date:
    """Easter Sunday of `year` in the Gregorian calendar."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _saturday_from(first: date) -> date:
    """The first Saturday on or after `first`."""
    return first + timedelta(days=(_SATURDAY - first.weekday()) % _DAYS_PER_WEEK)


@cache
def public_holidays(year: int) -> dict[date, str]:
    """The public holidays of `year` (Lag 1989:253), by date; Sundays not listed.

    Every Sunday is a holiday too, but no working day anyway.
    """
    easter = easter_sunday(year)
    holidays = {
        date(year, 1, 1): "nyårsdagen",
        date(year, 1, 6): "trettondedag jul",
        easter - timedelta(days=2): "långfredagen",
        easter: "påskdagen",
        easter + timedelta(days=1): "annandag påsk",
        date(year, 5, 1): "första maj",
        easter + timedelta(days=39): "Kristi himmelsfärdsdag",
        easter + timedelta(days=49): "pingstdagen",
        date(year, 6, 6): "Sveriges nationaldag",
        _saturday_from(date(year, 6, 20)): "midsommardagen",
        _saturday_from(date(year, 10, 31)): "alla helgons dag",
        date(year, 12, 25): "juldagen",
        date(year, 12, 26): "annandag jul",
    }
    return dict(sorted(holidays.items()))


@cache
def eves(year: int) -> dict[date, str]:
    """Midsommarafton, julafton and nyårsafton of `year`: no public holidays by law.

    Many treat them as holidays, and Lag (1930:173) does so for deadlines
    set by law, so `calculate_date` says when counting them would change
    its answer.
    """
    midsummer_eve = _saturday_from(date(year, 6, 20)) - timedelta(days=1)
    return {
        midsummer_eve: "midsommarafton",
        date(year, 12, 24): "julafton",
        date(year, 12, 31): "nyårsafton",
    }


def holiday_name(day: date) -> str | None:
    """The name of the public holiday on `day`; None for any other day."""
    return public_holidays(day.year).get(day)


def is_working_day(day: date, *, eves_off: bool = False) -> bool:
    """Monday to Friday and no public holiday (nor an eve, with `eves_off`)."""
    if day.weekday() >= _SATURDAY or day in public_holidays(day.year):
        return False
    return not (eves_off and day in eves(day.year))


def add_working_days(day: date, count: int, *, eves_off: bool = False) -> date:
    """The working day `count` working days after `day` (before it when negative).

    The start day is not counted, whether or not it is a working day: one
    working day after Friday 2026-12-25 is Monday 2026-12-28.
    """
    step = timedelta(days=1 if count > 0 else -1)
    remaining = abs(count)
    while remaining:
        day += step
        if is_working_day(day, eves_off=eves_off):
            remaining -= 1
    return day


def add_months(day: date, months: int) -> date:
    """`day` moved by `months`; the month's last day when the month is shorter."""
    index = day.month - 1 + months
    year, month = day.year + index // _MONTHS_PER_YEAR, index % _MONTHS_PER_YEAR + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def shift(day: date, amount: int, unit: Unit) -> date:
    """`day` moved by `amount` units, forwards when positive, backwards when negative."""
    if unit == "days":
        return day + timedelta(days=amount)
    if unit == "working_days":
        return add_working_days(day, amount)
    if unit == "weeks":
        return day + timedelta(weeks=amount)
    if unit == "months":
        return add_months(day, amount)
    return add_months(day, amount * _MONTHS_PER_YEAR)
