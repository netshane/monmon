"""Small shared helpers."""

from __future__ import annotations

import re
from datetime import timedelta


def to_number(value) -> float | int | None:
    """Coerce a value to a number, returning None when it is not numeric."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value

    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def to_bool(value) -> bool | None:
    """Coerce a value to a bool, returning None when unset or unrecognised.

    Toml booleans arrive as bools, but templated options can arrive as the
    strings "true" / "false" - `bool("false")` is True, so never use that.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0

    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False

    return None


def format_table(
    columns: list[str], rows: list[list], max_rows: int | None = None
) -> str:
    """Render a simple fixed width text table."""
    columns = [str(c) for c in columns]
    display_rows = [[("" if v is None else str(v)) for v in row] for row in rows]
    truncated = 0

    if max_rows is not None and len(display_rows) > max_rows:
        truncated = len(display_rows) - max_rows
        display_rows = display_rows[:max_rows]

    widths = [len(c) for c in columns]
    for row in display_rows:
        for index, value in enumerate(row[: len(widths)]):
            widths[index] = max(widths[index], len(value))

    lines = [
        "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns)),
        "  ".join("-" * w for w in widths),
    ]
    for row in display_rows:
        lines.append(
            "  ".join(
                (row[i] if i < len(row) else "").ljust(widths[i])
                for i in range(len(widths))
            )
        )

    if truncated:
        lines.append(f"... {truncated} more row(s)")

    return "\n".join(lines)


def format_html_table(columns: list[str], rows: list[list]) -> str:
    """Render an html table for report emails."""
    header = "".join(f"<th>{escape_html(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape_html(v)}</td>" for v in row) + "</tr>"
        for row in rows
    )

    return (
        '<table border="1" cellpadding="4" cellspacing="0">'
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def escape_html(value) -> str:
    """Escape a value for inclusion in report/notification html."""
    from html import escape

    return escape("" if value is None else str(value))


# interval units accepted by `parse_interval`; a bare number means minutes
_INTERVAL_UNITS = {
    "s": "seconds",
    "sec": "seconds",
    "secs": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "m": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "h": "hours",
    "hr": "hours",
    "hrs": "hours",
    "hour": "hours",
    "hours": "hours",
    "d": "days",
    "day": "days",
    "days": "days",
    "w": "weeks",
    "week": "weeks",
    "weeks": "weeks",
}


def parse_interval(value: str | None) -> timedelta | None:
    """Parse `"5 min"`, `"30s"`, `"2 hours"` into a timedelta.

    Lives here rather than on `ScheduleCalculator` because the notification
    throttle delays use the same syntax without belonging to scheduling.
    """
    if value is None:
        return None

    text = str(value).strip().lower()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-z]*)$", text)
    if not match:
        return None

    amount, unit = match.groups()
    unit_name = _INTERVAL_UNITS.get(unit or "minutes")
    if unit_name is None:
        return None

    try:
        return timedelta(**{unit_name: float(amount)})
    except (OverflowError, ValueError):
        # an interval too large for a timedelta is as unusable as an
        # unparseable one - callers treat None as "no delay configured"
        return None
