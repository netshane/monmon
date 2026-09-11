"""Data models describing a monitor definition loaded from a toml file."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from loguru import logger

from .contacts import CONTACT_TYPES, Contact, parse_contact_list
from .message_templates import MessageTemplateSet

# the instance name given to a `[test.<test_type>]` section written without
# one, so every test has an instance to build a default key from
DEFAULT_TEST_INSTANCE = "default"


@dataclass
class ScheduleConfig:
    """Schedule settings for a monitor.

    `cron` overrides every other setting except `start_time` / `end_time`.
    """

    cron: str | None = None
    daily: bool = False
    days_of_week: str | None = None
    repeat: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    # recheck overrides - None means "inherit the settings.toml default", an
    # explicit "0" turns the recheck off for this monitor.  The intervals are
    # left as written and parsed by ScheduleCalculator.parse_interval.
    recheck_after_skipped: str | None = None
    recheck_after_error: str | None = None
    recheck_after_alert: str | None = None
    recheck_max_attempts: int | None = None

    @property
    def is_scheduled(self) -> bool:
        return bool(self.cron or self.daily or self.repeat or self.days_of_week)

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleConfig":
        return cls(
            cron=_clean_str(data.get("cron")),
            daily=bool(data.get("daily", False)),
            days_of_week=_clean_str(data.get("days_of_week")),
            repeat=_clean_str(data.get("repeat")),
            start_time=parse_time_of_day(data.get("start_time")),
            end_time=parse_time_of_day(data.get("end_time")),
            recheck_after_skipped=_clean_str(data.get("recheck_after_skipped")),
            recheck_after_error=_clean_str(data.get("recheck_after_error")),
            recheck_after_alert=_clean_str(data.get("recheck_after_alert")),
            recheck_max_attempts=_clean_int(
                data.get("recheck_max_attempts"), field="schedule.recheck_max_attempts"
            ),
        )


@dataclass
class HierarchyConfig:
    """A single hierarchy (the primary one, or a named alternate)."""

    name: str
    node_name: str | None = None
    parent_node: str | None = None
    skip_on_parent_fail: bool = False
    alert_on_parent_fail: bool = False

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "HierarchyConfig":
        return cls(
            name=name,
            node_name=_clean_str(data.get("node_name")),
            parent_node=_clean_str(data.get("parent_node")),
            skip_on_parent_fail=bool(data.get("skip_on_parent_fail", False)),
            alert_on_parent_fail=bool(data.get("alert_on_parent_fail", False)),
        )


# `alert_email` and friends were replaced by the `<channel>:<target>` contact
# lists - a monitor still using them fails to load rather than silently
# notifying nobody
_REMOVED_CONTACT_KEYS = {
    "alert_email": "alert",
    "error_email": "error",
    "info_email": "info",
    "notify_email": "notify",
    "report_email": "report",
    "combine_alert_emails": "combine_alerts",
    "combine_report_emails": "combine_reports",
}


@dataclass
class ContactConfig:
    """Notification routing for a monitor, one contact list per type."""

    alert: list[Contact] = field(default_factory=list)
    error: list[Contact] = field(default_factory=list)
    info: list[Contact] = field(default_factory=list)
    notify: list[Contact] = field(default_factory=list)
    report: list[Contact] = field(default_factory=list)
    combine_reports: bool = True
    combine_alerts: bool = True
    # renotify overrides, one per contact type - None means "inherit the
    # settings.toml default", an explicit "0" turns the rate limiting off for
    # this monitor.  Left as written and parsed by `helpers.parse_interval`.
    renotify_after_alert: str | None = None
    renotify_after_report: str | None = None
    renotify_after_info: str | None = None
    renotify_after_notify: str | None = None
    renotify_after_error: str | None = None

    def renotify_after(self, contact_type: str) -> str | None:
        """This monitor's renotify override for a contact type, if it set one."""
        if contact_type not in CONTACT_TYPES:
            raise ValueError(
                f"Unknown contact type '{contact_type}' - "
                f"expected one of {', '.join(CONTACT_TYPES)}"
            )

        return getattr(self, f"renotify_after_{contact_type}")

    def for_type(self, contact_type: str) -> list[Contact]:
        """The contact list for one of `CONTACT_TYPES`."""
        if contact_type not in CONTACT_TYPES:
            raise ValueError(
                f"Unknown contact type '{contact_type}' - "
                f"expected one of {', '.join(CONTACT_TYPES)}"
            )

        return getattr(self, contact_type)

    @classmethod
    def from_dict(cls, data: dict) -> "ContactConfig":
        removed = [key for key in data if key in _REMOVED_CONTACT_KEYS]
        if removed:
            replacements = ", ".join(
                f"'{key}' -> '{_REMOVED_CONTACT_KEYS[key]}'" for key in sorted(removed)
            )
            raise ValueError(
                f"[contact] uses removed setting(s): {replacements}.  Contact lists "
                f"are now lists of '<channel>:<target>' strings, "
                f'e.g. alert = ["email:ops@example.com", "slack:#alerts"]'
            )

        return cls(
            **{
                contact_type: parse_contact_list(
                    data.get(contact_type), field=f"contact.{contact_type}"
                )
                for contact_type in CONTACT_TYPES
            },
            combine_reports=bool(data.get("combine_reports", True)),
            combine_alerts=bool(data.get("combine_alerts", True)),
            **{
                f"renotify_after_{contact_type}": _clean_str(
                    data.get(f"renotify_after_{contact_type}")
                )
                for contact_type in CONTACT_TYPES
            },
        )


@dataclass
class TestConfig:
    """A single test within a monitor.

    `test_type` is the section name under `[test.*]`, e.g. `ping`, `dbflag`, or
    `custom.magic_py` for custom module tests.  `instance` is the name that
    follows it in `[test.<test_type>.<instance>]`, letting one monitor declare
    several tests of the same type; a section written without one is
    `DEFAULT_TEST_INSTANCE`.
    """

    # keeps pytest from trying to collect this class as a test case
    __test__ = False

    test_type: str
    key: str
    options: dict = field(default_factory=dict)
    instance: str = DEFAULT_TEST_INSTANCE

    @property
    def full_name(self) -> str:
        """The `[test.*]` section path this test came from, for messages."""
        if self.instance == DEFAULT_TEST_INSTANCE:
            return self.test_type

        return f"{self.test_type}.{self.instance}"

    def get(self, name: str, default=None):
        return self.options.get(name, default)


@dataclass
class MonitorDefinition:
    """A fully resolved monitor (templates already merged in)."""

    name: str
    source_file: str
    description: str | None = None
    link: str | None = None
    template: str | None = None
    monitor_type_alert: bool = True
    monitor_type_report: bool = False
    active: bool = True
    tags: list[str] = field(default_factory=list)
    connections: dict = field(default_factory=dict)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    hierarchies: dict[str, HierarchyConfig] = field(default_factory=dict)
    contact: ContactConfig = field(default_factory=ContactConfig)
    tests: list[TestConfig] = field(default_factory=list)
    # compiled `[contact.message]` templates - built by the loader, so a
    # definition assembled by hand falls back to the notifier's own set
    message_templates: MessageTemplateSet | None = None
    raw: dict = field(default_factory=dict)


def parse_tags(value) -> list[str]:
    """Parse `tags` given as a list, or as a comma/space delimited string."""
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = str(value).replace(",", " ").split()

    tags = []
    for item in items:
        tag = item.strip()
        if tag and tag not in tags:
            tags.append(tag)

    return tags


def _clean_str(value) -> str | None:
    """Treat empty/whitespace-only toml strings as unset."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_int(value, field: str | None = None) -> int | None:
    """Read an optional whole-number toml value, `None` when it is unset.

    A value that is not a number is logged and treated as unset rather than
    failing the monitor load - the same way an unparseable recheck interval is
    handled in `ScheduleCalculator`.
    """
    text = _clean_str(value)
    if text is None:
        return None

    try:
        return int(float(text))
    except (ValueError, OverflowError):
        logger.error(f"Invalid {field or 'numeric'} value '{value}' - ignoring")
        return None


def parse_time_of_day(value) -> time | None:
    """Parse `HH:MM`, `HH:MM:SS` or `HHMM` style time-of-day values."""
    text = _clean_str(value)
    if text is None:
        return None

    if isinstance(value, time):
        return value

    for fmt in ("%H:%M:%S", "%H:%M", "%H%M", "%I:%M %p", "%I %p"):
        try:
            from datetime import datetime

            return datetime.strptime(text.upper(), fmt).time()
        except ValueError:
            continue

    raise ValueError(f"Unable to parse time of day: '{value}'")
