"""Expands Jinja `{{ ... }}` templates found in monitor test options."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from jinja2 import Environment, StrictUndefined


class ValueExpander:
    """Renders Jinja templates embedded in monitor test option values.

    Context variables (built fresh from `now_provider()` on every call):

    * `now`, `utcnow`, `today`, `yesterday`, `tomorrow` - datetimes. `today`,
      `yesterday` and `tomorrow` are at midnight; `utcnow` is the real current
      UTC time, independent of `now_provider`'s timezone.
    * `epoch`, `epoch_ms` - the current unix time, as ints.
    * `now_iso_format`, `utcnow_iso_format`, `today_iso_format`,
      `yesterday_iso_format`, `tomorrow_iso_format` - the isoformat string of
      the corresponding datetime above.

    Globals: `timedelta`, `date`, `datetime`.

    Filters: `isoformat`, `strftime`, `date`, `epoch`, `epoch_ms`.

    Undefined variables raise `jinja2.exceptions.UndefinedError` (via
    `StrictUndefined`), so a typo fails the test rather than passing through
    silently.
    """

    def __init__(self, now_provider: Callable[[], datetime] | None = None):
        self.now_provider = now_provider or datetime.now

        self._env = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self._env.globals.update(timedelta=timedelta, date=date, datetime=datetime)
        self._env.filters.update(
            isoformat=self._filter_isoformat,
            strftime=self._filter_strftime,
            date=self._filter_date,
            epoch=self._filter_epoch,
            epoch_ms=self._filter_epoch_ms,
        )
        self._template_cache: dict[str, Any] = {}

    def expand(self, value):
        """Expand templates in strings, lists, tuples and dicts, recursively."""
        if isinstance(value, str):
            return self.expand_text(value)
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.expand(item) for item in value)
        if isinstance(value, dict):
            return {k: self.expand(v) for k, v in value.items()}

        return value

    def expand_text(self, text: str) -> str:
        """Render a single string as a Jinja template."""
        if not any(marker in text for marker in ("{{", "{%", "{#")):
            return text

        template = self._template_cache.get(text)
        if template is None:
            template = self._env.from_string(text)
            self._template_cache[text] = template

        return template.render(self._context())

    def _context(self) -> dict[str, Any]:
        now = self.now_provider()
        utcnow = datetime.now(timezone.utc)
        today = datetime.combine(now.date(), datetime.min.time())
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)

        return {
            "now": now,
            "utcnow": utcnow,
            "today": today,
            "yesterday": yesterday,
            "tomorrow": tomorrow,
            "epoch": int(now.timestamp()),
            "epoch_ms": int(now.timestamp() * 1000),
            "now_iso_format": now.isoformat(),
            "utcnow_iso_format": utcnow.isoformat(),
            "today_iso_format": today.isoformat(),
            "yesterday_iso_format": yesterday.isoformat(),
            "tomorrow_iso_format": tomorrow.isoformat(),
        }

    @staticmethod
    def _filter_isoformat(value: datetime | date) -> str:
        return value.isoformat()

    @staticmethod
    def _filter_strftime(value: datetime | date, fmt: str) -> str:
        return value.strftime(fmt)

    @staticmethod
    def _filter_date(value: datetime) -> date:
        return value.date()

    @staticmethod
    def _filter_epoch(value: datetime) -> int:
        return int(value.timestamp())

    @staticmethod
    def _filter_epoch_ms(value: datetime) -> int:
        return int(value.timestamp() * 1000)
