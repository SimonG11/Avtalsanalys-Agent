"""Numbers and Markdown the Swedish way, for the answer evaluation's reports.

What:
    `md` puts a text on one line with markup escaped, `md_row` makes a
    table row, and `decimal`, `plain_number`, `percent`, `in_seconds`,
    `in_dollars`, `dollar_range` and `thousands` write numbers: "7,5",
    "95 %", "37 s", "0,067–0,080 USD", "13 175".

Why:
    A run's report (`answer_report`) and the comparison of two runs
    (`compare_answer_runs`) show the same numbers, so they write them with
    the same functions: a cost in one must read as it does in the other.

How:
    A decimal comma, and a no-break space (NBSP) before a unit and between
    thousands, so "95 %" never breaks across lines. A dollar amount under
    0,1 has three decimals, so a question's cost does not round to 0,00.
"""

from collections.abc import Sequence

NBSP = "\u00a0"  # between thousands and before a unit: "13 175", "95 %", "60 s"
_MARKUP = str.maketrans({char: "\\" + char for char in "\\`*_[]<>|"})


def md(text: str) -> str:
    """Text for a table cell or a line: on one line, with markup characters escaped."""
    return " ".join(text.split()).translate(_MARKUP)


def md_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def decimal(value: float, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def plain_number(value: float) -> str:
    """A whole number as it is, else with one decimal: '7', '7,5'."""
    return str(int(value)) if value == int(value) else decimal(value, 1)


def percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole) if whole else 0}{NBSP}%"


def in_seconds(value: float) -> str:
    return f"{round(value)}{NBSP}s"


def in_dollars(value: float) -> str:
    return f"{decimal(value, 2 if value >= 0.1 else 3)}{NBSP}USD"


def dollar_range(low: float, high: float) -> str:
    """'0,12–0,26 USD'; one amount when both are the same as written."""
    low_text, high_text = in_dollars(low), in_dollars(high)
    if low_text == high_text:
        return high_text
    return f"{low_text.removesuffix(f'{NBSP}USD')}–{high_text}"


def thousands(value: int) -> str:
    return f"{value:,}".replace(",", NBSP)
