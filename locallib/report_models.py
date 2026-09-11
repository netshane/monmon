"""Models describing a report definition loaded from `reports/<name>/report.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# sections a report may switch on or off
SECTION_NAMES = ("summary", "successes", "run_times", "errors", "alerts")

_DURATION_UNITS = {
    "s": "seconds",
    "sec": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "m": "minutes",
    "min": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "h": "hours",
    "hr": "hours",
    "hour": "hours",
    "hours": "hours",
    "d": "days",
    "day": "days",
    "days": "days",
    "w": "weeks",
    "week": "weeks",
    "weeks": "weeks",
}

_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*$", re.IGNORECASE)


class ReportConfigError(ValueError):
    """Raised when a report.yaml cannot be understood."""


def parse_duration(value) -> timedelta:
    """Parse a relative window such as `30d`, `12 hours`, or `90m`."""
    if isinstance(value, timedelta):
        return value

    match = _DURATION_PATTERN.match(str(value))
    if not match:
        raise ReportConfigError(f"Unable to parse duration: '{value}'")

    amount, unit = match.group(1), (match.group(2) or "d").lower()
    if unit not in _DURATION_UNITS:
        raise ReportConfigError(f"Unknown duration unit '{unit}' in '{value}'")

    return timedelta(**{_DURATION_UNITS[unit]: float(amount)})


def parse_timestamp(value) -> datetime:
    """Parse a `since` / `until` bound given as a date, datetime, or string."""
    if isinstance(value, datetime):
        return value

    from datetime import date

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    raise ReportConfigError(f"Unable to parse timestamp: '{value}'")


@dataclass
class FieldSpec:
    """One field of one monitor, and how much of its history to return.

    `mode` is `latest` (a single most recent value) or `range` (a list of
    values bounded by `last`, or by `since` / `until`).
    """

    name: str
    mode: str = "latest"
    last: timedelta | None = None
    since: datetime | None = None
    until: datetime | None = None
    label: str | None = None

    @property
    def is_range(self) -> bool:
        return self.mode == "range"

    def window(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        """Resolve the field's window to absolute (since, until) bounds."""
        if not self.is_range:
            return None, None

        until = self.until
        since = self.since
        if since is None and self.last is not None:
            since = (until or now) - self.last

        return since, until

    @classmethod
    def from_dict(cls, name: str, data) -> "FieldSpec":
        if data is None:
            data = {}
        if isinstance(data, str):
            data = {"mode": data}
        if not isinstance(data, dict):
            raise ReportConfigError(f"Field '{name}' must be a mapping or a mode name")

        mode = str(data.get("mode", "latest")).strip().lower()
        if mode not in ("latest", "range"):
            raise ReportConfigError(
                f"Field '{name}' has unknown mode '{mode}' (expected latest or range)"
            )

        return cls(
            name=name,
            mode=mode,
            last=parse_duration(data["last"]) if data.get("last") else None,
            since=parse_timestamp(data["since"]) if data.get("since") else None,
            until=parse_timestamp(data["until"]) if data.get("until") else None,
            label=data.get("label"),
        )


@dataclass
class MonitorSelector:
    """Selects the monitors an entry applies to, and the fields to resolve."""

    match: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    fields: dict[str, FieldSpec] = field(default_factory=dict)

    def matches(self, name: str, tags: list[str] | None = None) -> bool:
        """True when `name` (or its tags) is selected and not excluded."""
        from fnmatch import fnmatchcase

        if any(fnmatchcase(name, pattern) for pattern in self.exclude):
            return False

        selected = not self.match and not self.tags

        if self.match and any(fnmatchcase(name, pattern) for pattern in self.match):
            selected = True
        if self.tags and any(tag in (tags or []) for tag in self.tags):
            selected = True

        return selected

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorSelector":
        if not isinstance(data, dict):
            raise ReportConfigError("Each monitors entry must be a mapping")

        fields = {
            name: FieldSpec.from_dict(name, spec)
            for name, spec in (data.get("fields") or {}).items()
        }

        return cls(
            match=_as_list(data.get("match") or data.get("monitor") or data.get("id")),
            tags=_as_list(data.get("tags") or data.get("tag")),
            exclude=_as_list(data.get("exclude")),
            fields=fields,
        )


@dataclass
class ReportConfig:
    """A whole report definition."""

    name: str
    title: str
    path: str
    template: str = "template.html.j2"
    subtitle: str | None = None
    branding: dict = field(default_factory=dict)
    sections: dict[str, bool] = field(default_factory=dict)
    monitors: list[MonitorSelector] = field(default_factory=list)
    default_window: timedelta | None = None
    extra: dict = field(default_factory=dict)

    def section_enabled(self, name: str) -> bool:
        return bool(self.sections.get(name, True))

    @classmethod
    def from_dict(cls, name: str, path: str, data: dict) -> "ReportConfig":
        data = data or {}
        if not isinstance(data, dict):
            raise ReportConfigError(f"Report '{name}' config must be a mapping")

        sections = _parse_sections(data.get("sections"))
        monitors = [
            MonitorSelector.from_dict(entry) for entry in (data.get("monitors") or [])
        ]

        known = {
            "title",
            "subtitle",
            "branding",
            "sections",
            "monitors",
            "template",
            "default_window",
        }

        return cls(
            name=name,
            title=str(data.get("title") or name),
            path=path,
            template=str(data.get("template") or "template.html.j2"),
            subtitle=data.get("subtitle"),
            branding=dict(data.get("branding") or {}),
            sections=sections,
            monitors=monitors,
            default_window=parse_duration(data["default_window"])
            if data.get("default_window")
            else None,
            extra={k: v for k, v in data.items() if k not in known},
        )


def _parse_sections(value) -> dict[str, bool]:
    """Sections may be a list of names to show, or a name -> bool mapping."""
    if value is None:
        return {name: True for name in SECTION_NAMES}

    if isinstance(value, dict):
        # sections not mentioned stay on
        return {
            name: bool(value.get(name, True))
            for name in set(SECTION_NAMES) | set(value)
        }

    enabled = {str(v).strip() for v in _as_list(value)}
    return {name: name in enabled for name in set(SECTION_NAMES) | enabled}


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]

    return [str(value)]
