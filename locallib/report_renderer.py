"""Renders a report's Jinja2 template into html.

Templates are loaded from the report's own folder, so a report may split its
markup across several files (`{% include %}`) and still stay self contained.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    Undefined,
    select_autoescape,
)
from markupsafe import Markup, escape

from .helpers import to_number
from .report_models import ReportConfig


class ReportRenderer:
    """Renders report templates with a small set of presentation filters."""

    def __init__(self, strict: bool = False):
        self.strict = strict

    def render(self, config: ReportConfig, context: dict) -> str:
        environment = self.environment(config.path)

        return environment.get_template(config.template).render(**context)

    def environment(self, template_path: str) -> Environment:
        environment = Environment(
            loader=FileSystemLoader(template_path),
            autoescape=select_autoescape(default_for_string=True, default=True),
            undefined=StrictUndefined if self.strict else Undefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        environment.filters.update(
            {
                "datetime": format_datetime,
                "duration": format_duration,
                "number": format_number,
                "percent": format_percent,
                "sparkline": sparkline,
            }
        )

        return environment


def format_datetime(value, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not isinstance(value, datetime):
        return "" if value is None else str(value)

    return value.strftime(fmt)


def format_duration(value, digits: int = 2) -> str:
    """Format a number of seconds, falling back to a plain string."""
    if isinstance(value, timedelta):
        return f"{value.total_seconds():.{digits}f}s"

    number = to_number(value)
    if number is None:
        return "" if value is None else str(value)

    return f"{number:.{digits}f}s"


def format_number(value, digits: int = 2) -> str:
    number = to_number(value)
    if number is None:
        return "" if value is None else str(value)

    if float(number).is_integer():
        return str(int(number))

    return f"{number:.{digits}f}"


def format_percent(value, digits: int = 1) -> str:
    number = to_number(value)
    if number is None:
        return "-"

    return f"{number:.{digits}f}%"


def sparkline(
    values, width: int = 120, height: int = 24, stroke: str = "currentColor"
) -> Markup:
    """An inline svg sparkline - no javascript, no external assets."""
    numbers = [n for n in (to_number(v) for v in values or []) if n is not None]

    if len(numbers) < 2:
        return Markup('<svg class="sparkline" aria-hidden="true"></svg>')

    # `stroke` may come from report data - it is built into markup below, so
    # it never reaches the page unescaped
    stroke = escape(stroke)
    low, high = min(numbers), max(numbers)
    span = (high - low) or 1
    step = width / (len(numbers) - 1)

    points = " ".join(
        f"{index * step:.1f},{height - ((value - low) / span) * (height - 2) - 1:.1f}"
        for index, value in enumerate(numbers)
    )

    return Markup(
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" preserveAspectRatio="none" role="img" '
        f'aria-label="trend of {len(numbers)} values">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="1.5" '
        f'stroke-linejoin="round" points="{points}" /></svg>'
    )
