"""calculate_date: a date some days, working days, weeks, months or years from another.

What:
    `calculate_date` moves a start date by an amount of one unit, after or
    before it, and returns the date, its weekday, the calculation as one
    line (`step`, e.g. "2027-02-17 minus 3 månader = 2026-11-17"), the
    public holidays on weekdays that were not counted as working days, and
    notes on what the answer depends on: a month without the start's day,
    a result on a weekend or a holiday, or an eve that would change it.

Why:
    A notice period or a deadline in working days is easy for a model to
    get a day wrong (architecture plan, section 5: "LLM:er räknar dåligt").
    The register rule redoes the step the answer writes with the same
    functions (`domain/dates.py`), so the answer is checked without
    trusting this tool's result in the message history, which a client
    sends. The tool reads no database: it is pure arithmetic, behind the
    same MCP server as the other tools so that the agent has one place for
    its tools (ADR 0003).

How:
    `domain.dates.shift`, with the amount negative for "before". Working
    days are Monday to Friday except the public holidays of Lag (1989:253);
    the start day is not counted. Midsommarafton, julafton and nyårsafton
    are working days by that reading, as the agreements' definition
    ("en i Sverige helgfri måndag till fredag") says; when counting them as
    holidays would give another date, a note gives that date. With
    `include_start` the start day belongs to the period, so the result
    moves a day back towards the start: 48 months from 2024-11-14 ends
    2028-11-13, as the register's dates do. A calendar result on a weekend
    or a holiday is not moved; a note says so. A result outside the years
    the holidays hold for (`dates.FIRST_DAY` to `dates.LAST_DAY`) is an
    `ArgumentError`, as a start outside them is refused by its type.
"""

from datetime import date, timedelta

from pydantic import BaseModel

from avtalsagent.domain import dates
from avtalsagent.mcp_server.arguments import Amount, DateUnit, Direction, IncludeStart, StartDate
from avtalsagent.mcp_server.errors import ArgumentError

# How the step writes each unit, in the singular and the plural.
_UNIT_WORDS = {
    "days": ("dag", "dagar"),
    "working_days": ("arbetsdag", "arbetsdagar"),
    "weeks": ("vecka", "veckor"),
    "months": ("månad", "månader"),
    "years": ("år", "år"),
}
_MONTH_NAMES = (
    "januari",
    "februari",
    "mars",
    "april",
    "maj",
    "juni",
    "juli",
    "augusti",
    "september",
    "oktober",
    "november",
    "december",
)
_SATURDAY = 5


class Holiday(BaseModel):
    """A public holiday on a weekday, not counted as a working day."""

    day: date
    name: str  # e.g. "juldagen"


class DateResult(BaseModel):
    result: date
    weekday: str  # in Swedish, e.g. "fredag"
    step: str  # the calculation for the answer: "2027-02-17 minus 3 månader = 2026-11-17"
    skipped: list[Holiday]  # working days only: the weekday holidays that were not counted
    notes: list[str]  # in Swedish: what the result depends on


def calculate_date(
    start: StartDate,
    amount: Amount,
    unit: DateUnit,
    direction: Direction,
    include_start: IncludeStart = False,
) -> DateResult:
    """Räkna fram ett datum några dagar, arbetsdagar, veckor, månader eller år från ett annat.

    Använd det för uppsägningstider, frister, förlängningar och perioder i stället för att
    räkna själv. working_days räknar Arbetsdagar: måndag–fredag utom allmänna helgdagar.
    Läs först hur avtalet definierar Arbetsdag. Startdagen räknas inte, så en arbetsdag efter
    en fredag är måndagen. Midsommarafton, julafton och nyårsafton räknas som arbetsdagar;
    notes säger om det ändrar datumet. En månad från den 31:a blir månadens sista dag när
    månaden är kortare. Skriv step ordagrant i svaret, i samma mening som datumet: svaret
    kontrolleras genom att steget räknas om.
    """
    if include_start and unit == "working_days":
        raise ArgumentError(
            "include_start gäller perioder i dagar, veckor, månader eller år, inte "
            "working_days: räkna arbetsdagarna från dagen före den första."
        )
    sign = 1 if direction == "after" else -1
    moved = _shifted(start, sign * amount, unit)
    result = moved - timedelta(days=sign) if include_start else moved
    singular, plural = _UNIT_WORDS[unit]
    step = (
        f"{start.isoformat()} {'plus' if sign > 0 else 'minus'} {amount} "
        f"{singular if amount == 1 else plural} = {moved.isoformat()}"
    )
    if include_start:
        step += f", {'minus' if sign > 0 else 'plus'} 1 dag = {result.isoformat()}"
    notes: list[str] = []
    skipped: list[Holiday] = []
    if unit in ("months", "years") and moved.day < start.day:
        notes.append(
            f"{_capital(_MONTH_NAMES[moved.month - 1])} {moved.year} har ingen dag "
            f"{start.day}: månadens sista dag används."
        )
    if unit == "working_days":
        skipped = _skipped(start, moved)
        notes += _eve_notes(start, moved, sign * amount)
    else:
        notes += _day_off_notes(result)
    return DateResult(
        result=result,
        weekday=dates.WEEKDAYS[result.weekday()],
        step=step,
        skipped=skipped,
        notes=notes,
    )


def _shifted(start: date, amount: int, unit: dates.Unit) -> date:
    """`dates.shift`, refused when the result falls outside the years the holidays hold for."""
    try:
        moved = dates.shift(start, amount, unit)
    except (ValueError, OverflowError):
        moved = None
    if moved is None or not dates.FIRST_DAY <= moved <= dates.LAST_DAY:
        raise ArgumentError(
            f"Resultatet hamnar utanför {dates.FIRST_DAY.isoformat()} till "
            f"{dates.LAST_DAY.isoformat()}, de år kalenderns helgdagar gäller för. Kontrollera "
            "startdatumet och antalet."
        )
    return moved


def _between(start: date, end: date) -> list[date]:
    """The days after `start` up to and including `end`, in either direction."""
    step = 1 if end >= start else -1
    return [start + timedelta(days=step * n) for n in range(1, abs((end - start).days) + 1)]


def _skipped(start: date, end: date) -> list[Holiday]:
    """The public holidays on weekdays that the count passed, in date order."""
    days = sorted(_between(start, end))
    return [
        Holiday(day=day, name=name)
        for day in days
        if day.weekday() < _SATURDAY and (name := dates.holiday_name(day)) is not None
    ]


def _eve_notes(start: date, end: date, count: int) -> list[str]:
    """A note when counting the eves as holidays would give another date.

    It names every eve that count passes, also one after `end`: from
    2026-12-23, four working days are 2026-12-30, and 2027-01-04 only with
    both julafton and nyårsafton off.
    """
    other = dates.add_working_days(start, count, eves_off=True)
    if other == end:
        return []
    counted = [
        f"{dates.eves(day.year)[day]} {day.isoformat()}"
        for day in sorted(_between(start, other))
        if day in dates.eves(day.year) and dates.is_working_day(day)
    ]
    one = len(counted) == 1
    return [
        f"{_capital(_joined(counted))} räknas som arbetsdag{'' if one else 'ar'}, eftersom "
        f"aftnarna inte är allmänna helgdagar. Räknas {'den' if one else 'de'} som "
        f"helgdag{'' if one else 'ar'} blir datumet {other.isoformat()}."
    ]


def _day_off_notes(day: date) -> list[str]:
    """A note when a calendar result is no working day; the date is not moved."""
    if (name := dates.holiday_name(day)) is not None:
        what = f"{name}, en allmän helgdag"
    elif day.weekday() >= _SATURDAY:
        what = f"en {dates.WEEKDAYS[day.weekday()]}"
    else:
        return []
    return [
        f"{day.isoformat()} är {what}. Datumet flyttas inte; säger avtalet vad som gäller då, "
        "följ det."
    ]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _joined(items: list[str]) -> str:
    """The items as a Swedish list: "a", "a och b", "a, b och c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} och {items[-1]}"
