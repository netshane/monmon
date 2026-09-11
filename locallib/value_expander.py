"""Expands `{{>token<}}` placeholders found in monitor queries and arguments."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

from loguru import logger


class ValueExpander:
    """Replaces `{{>token<}}` placeholders with evaluated values.

    Supported tokens (all case insensitive):

    * `now`, `now_iso_format`, `utcnow`, `utcnow_iso_format`
    * `today`, `today_iso_format`, `yesterday`, `yesterday_iso_format`,
      `tomorrow`, `tomorrow_iso_format`
    * `epoch`, `epoch_ms`
    * `now-5min`, `now+2 hours`, `today-7d` - an offset applied to a base token
    * `strftime:%Y/%m/%d` - the current time formatted with the given pattern

    A `yesterday_io_format` spelling is accepted as an alias of
    `yesterday_iso_format` because it appears in the sample monitor file.
    """

    PATTERN = re.compile(r"\{\{>\s*(.*?)\s*<\}\}")

    _UNITS = {
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

    def __init__(self, now_provider: Callable[[], datetime] | None = None):
        self.now_provider = now_provider or datetime.now

    def expand(self, value):
        """Expand placeholders in strings, lists, and dicts, recursively."""
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
        def replace(match: re.Match) -> str:
            token = match.group(1)
            try:
                return self.evaluate(token)
            except ValueError as e:
                logger.warning(f"Unable to expand token '{token}': {e}")
                return match.group(0)

        return self.PATTERN.sub(replace, text)

    def evaluate(self, token: str) -> str:
        """Evaluate a single token and return its string representation."""
        token = token.strip()
        if not token:
            raise ValueError("empty token")

        lowered = token.lower()

        if lowered.startswith("strftime:"):
            return self.now_provider().strftime(token.split(":", 1)[1])

        base_token, delta = self._split_offset(lowered)
        value = self._base_value(base_token)

        if delta is not None:
            value = value + delta

        return self._format(base_token, value)

    def _split_offset(self, token: str) -> tuple[str, timedelta | None]:
        match = re.match(r"^([a-z_]+)\s*([+-])\s*(\d+)\s*([a-z]+)$", token)
        if not match:
            return token, None

        base_token, sign, amount, unit = match.groups()
        unit_name = self._UNITS.get(unit)
        if unit_name is None:
            raise ValueError(f"unknown time unit '{unit}'")

        delta = timedelta(**{unit_name: int(amount)})
        return base_token, -delta if sign == "-" else delta

    def _base_value(self, token: str) -> datetime:
        now = self.now_provider()
        midnight = datetime.combine(now.date(), datetime.min.time())

        if token in ("now", "now_iso_format", "epoch", "epoch_ms"):
            return now
        if token in ("utcnow", "utcnow_iso_format"):
            return now
        if token in ("today", "today_iso_format"):
            return midnight
        if token in ("yesterday", "yesterday_iso_format", "yesterday_io_format"):
            return midnight - timedelta(days=1)
        if token in ("tomorrow", "tomorrow_iso_format"):
            return midnight + timedelta(days=1)

        raise ValueError(f"unknown token '{token}'")

    @staticmethod
    def _format(token: str, value: datetime) -> str:
        if token == "epoch":
            return str(int(value.timestamp()))
        if token == "epoch_ms":
            return str(int(value.timestamp() * 1000))
        if token.endswith("_iso_format") or token.endswith("_io_format"):
            return value.isoformat()
        if token in ("today", "yesterday", "tomorrow"):
            return value.date().isoformat()

        return value.isoformat()
