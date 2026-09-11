"""Rate limits repeat notifications for a monitor that keeps failing.

A monitor left in `alert` or `error` comes off its normal cadence onto its
`recheck_after_<status>` delay (see `schedule_calculator`), so it runs - and
notifies - far more often than usual.  The gate here holds those repeats back:
once a notification of a given type has been delivered, the next one waits out
that type's `renotify_after_<type>` delay.

The window is fixed, measured from the last *successful* delivery, and kept per
monitor and contact type in the `notification_state` table so it survives
between `run-scheduled` invocations.  Alert and error timers additionally reset
whenever the monitor's status changes - a recovery, or a move from `alert` to
`error`, is news whatever the delay says.

Nothing in this module reads settings - `dependencies.get_notify_throttle()`
builds the `NotifyThrottle` and injects it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from loguru import logger

from .contacts import CONTACT_TYPES
from .helpers import parse_interval
from .monitor_repository import MonitorRepository

# the contact types whose timers reset on a status change.  `report`, `info`,
# and `notify` fire on healthy runs too, so resetting them on every `ok` would
# leave them never throttled at all
STATUS_RESET_TYPES = ("alert", "error")


@dataclass
class NotifyThrottle:
    """The configured `renotify_after_<type>` defaults.

    A `None` delay - or a zero one - means that contact type is not throttled,
    which is the shipped default.
    """

    delays: dict[str, timedelta | None] = field(default_factory=dict)

    def delay_for(self, contact_type: str) -> timedelta | None:
        return self.delays.get(contact_type)


class NotifyThrottleGate:
    """Decides whether a notification is due, and records the ones delivered.

    `clock` is injected so tests can move the window without sleeping.
    """

    def __init__(
        self,
        repository: MonitorRepository,
        throttle: NotifyThrottle,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.repository = repository
        self.throttle = throttle
        self.clock = clock
        # one run can suppress the same monitor several times; only the state
        # read is worth caching, and only for the length of that run
        self._state: dict[str, dict[str, dict]] = {}

    def allow(
        self,
        monitor_name: str,
        contact_type: str,
        status: str | None = None,
        override: str | None = None,
    ) -> bool:
        """True when a notification of this type may be sent right now.

        `override` is the monitor's own `renotify_after_<type>` value, left as
        written; `None` inherits the settings default and an explicit `0` turns
        throttling off for this monitor.
        """
        delay = self._delay(contact_type, override)
        if delay is None:
            return True

        entry = self._entry(monitor_name, contact_type)
        if entry is None:
            return True

        last_sent_at = entry.get("last_sent_at")
        if not isinstance(last_sent_at, datetime):
            return True

        # a status change is news regardless of the window, but only for the
        # types whose notifications are driven by status in the first place
        if contact_type in STATUS_RESET_TYPES and status != entry.get("last_status"):
            return True

        due_at = last_sent_at + delay
        now = self.clock()
        if now >= due_at:
            return True

        logger.info(
            f"Monitor '{monitor_name}': the '{contact_type}' notification was "
            f"blocked by rate limiting - the last one was delivered at "
            f"{last_sent_at:%Y-%m-%d %H:%M:%S} and the next is due at "
            f"{due_at:%Y-%m-%d %H:%M:%S}"
        )
        return False

    def record(self, monitor_name: str, contact_type: str, status: str | None = None):
        """Note that a notification of this type was delivered."""
        sent_at = self.clock()
        self.repository.record_notification(
            monitor_name=monitor_name,
            contact_type=contact_type,
            sent_at=sent_at,
            status=status,
        )
        self._state.setdefault(monitor_name, {})[contact_type] = {
            "last_sent_at": sent_at,
            "last_status": status,
        }

    def reset(self, monitor_name: str, overrides: dict[str, str | None] | None = None):
        """Clear the status driven timers after a monitor recovers.

        `overrides` are the monitor's own `renotify_after_<type>` values.  A
        type that is not rate limited has no timer to clear, so a healthy run
        of an unthrottled monitor - the shipped default - costs no write at
        all.
        """
        overrides = overrides or {}
        tracked = [
            contact_type
            for contact_type in STATUS_RESET_TYPES
            if self._delay(contact_type, overrides.get(contact_type)) is not None
        ]
        if not tracked:
            return

        self.repository.clear_notification_state(monitor_name, contact_types=tracked)
        cached = self._state.get(monitor_name)
        if cached:
            for contact_type in tracked:
                cached.pop(contact_type, None)

    # -- internals ---------------------------------------------------------

    def _delay(self, contact_type: str, override: str | None) -> timedelta | None:
        """The delay in force for one contact type, `None` when unthrottled."""
        if contact_type not in CONTACT_TYPES:
            return None

        if override is None:
            return self.throttle.delay_for(contact_type) or None

        delay = parse_interval(override)
        if delay is None:
            logger.error(
                f"Invalid renotify_after_{contact_type} interval '{override}' - "
                "this contact type will not be rate limited"
            )
            return None

        # an explicit 0 turns the rate limiting off for this monitor
        return delay or None

    def _entry(self, monitor_name: str, contact_type: str) -> dict | None:
        if monitor_name not in self._state:
            self._state[monitor_name] = self.repository.get_notification_state(
                monitor_name
            )

        return self._state[monitor_name].get(contact_type)
