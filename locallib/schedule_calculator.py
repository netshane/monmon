"""Works out when a monitor should next run."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from croniter import croniter
from loguru import logger

from .helpers import parse_interval
from .monitor_models import ScheduleConfig

_DAY_TOKENS = {
    "m": 0,
    "mo": 0,
    "mon": 0,
    "monday": 0,
    "tu": 1,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "w": 2,
    "we": 2,
    "wed": 2,
    "wednesday": 2,
    "th": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "f": 4,
    "fr": 4,
    "fri": 4,
    "friday": 4,
    "sa": 5,
    "sat": 5,
    "saturday": 5,
    "s": 6,
    "su": 6,
    "sun": 6,
    "sunday": 6,
}

_MAX_WINDOW_DAYS = 366

# the statuses a monitor can be left in that mean "this did not settle" - each
# has its own `recheck_after_<status>` delay
RECHECK_STATUSES = ("skipped", "error", "alert")


class ScheduleCalculator:
    """Computes next run times from a `[schedule]` section.

    Precedence follows the monitor file documentation: when `cron` is set it
    determines the cadence and only `start_time` / `end_time` still apply.
    Otherwise `repeat` gives the cadence within the daily window, `daily`
    means once per day, and `days_of_week` restricts which days are eligible.
    """

    def __init__(
        self,
        now_provider=None,
        recheck_delays: dict[str, timedelta | None] | None = None,
        recheck_max_attempts: int = 0,
    ):
        self.now_provider = now_provider or datetime.now
        # settings.toml defaults, overridable per monitor in `[schedule]`
        self.recheck_delays = recheck_delays or {}
        self.recheck_max_attempts = recheck_max_attempts

    def now(self) -> datetime:
        return self.now_provider()

    def next_run(
        self,
        schedule: ScheduleConfig,
        last_run: datetime | None = None,
        now: datetime | None = None,
        last_status: str | None = None,
        attempt_count: int = 0,
    ) -> datetime | None:
        """Return the next datetime the monitor should run, or None if it is
        only ever run manually.

        A monitor left in one of `RECHECK_STATUSES` comes off its normal
        cadence and is retried `recheck_after_<status>` after its last run,
        until it runs `ok` again or `recheck_max_attempts` is used up.  Manual
        monitors never recheck - they have no schedule to come off.
        """
        now = now or self.now()

        if not schedule.is_scheduled:
            return None

        candidate = self._next_recheck(schedule, last_run, last_status, attempt_count)
        is_recheck = candidate is not None

        if candidate is None:
            if schedule.cron:
                candidate = self._next_cron(schedule, last_run, now)
            elif schedule.repeat:
                candidate = self._next_repeat(schedule, last_run, now)
            else:
                candidate = self._next_daily(schedule, last_run, now)

        if candidate is None:
            return None

        # a recheck time is `last_run + delay`, not a cron occurrence, so on a
        # cron monitor it is the only candidate that still needs the cron's own
        # day restriction applied to it
        return self._advance_into_window(
            schedule, candidate, cron_days=is_recheck and bool(schedule.cron)
        )

    def is_due(
        self,
        schedule: ScheduleConfig,
        last_run: datetime | None = None,
        now: datetime | None = None,
        last_status: str | None = None,
        attempt_count: int = 0,
    ) -> bool:
        """True when the monitor's next run time has already passed and `now`
        is still inside the start_time/end_time window - a backlogged run
        (e.g. computed before the window opened) does not fire once the
        window has since closed."""
        now = now or self.now()
        next_run = self.next_run(
            schedule,
            last_run,
            now,
            last_status=last_status,
            attempt_count=attempt_count,
        )

        if next_run is None or next_run > now:
            return False

        return self._time_in_window(schedule, now.time())

    @staticmethod
    def _time_in_window(schedule: ScheduleConfig, t: time) -> bool:
        start = schedule.start_time
        end = schedule.end_time

        if start is not None and end is not None and end < start:
            # overnight window (e.g. 22:00-06:00) wraps midnight
            return t >= start or t <= end

        if start is not None and t < start:
            return False

        if end is not None and t > end:
            return False

        return True

    def rechecks_possible(self, schedules) -> bool:
        """True when any of these schedules could produce a recheck.

        Callers use this to skip gathering attempt counts entirely when the
        feature is switched off, which is the shipped default.  It is
        deliberately conservative - an explicit `0` on a monitor counts as
        "possible" rather than being parsed here.
        """
        if any(self.recheck_delays.values()):
            return True

        return any(
            getattr(schedule, f"recheck_after_{status}", None) is not None
            for schedule in schedules
            for status in RECHECK_STATUSES
        )

    def _next_recheck(
        self,
        schedule: ScheduleConfig,
        last_run: datetime | None,
        last_status: str | None,
        attempt_count: int,
    ) -> datetime | None:
        """The recheck time for a monitor left in a non-ok status, or None
        when no recheck applies and the normal cadence stands."""
        if last_run is None or not last_status:
            return None

        delay = self._recheck_delay(schedule, str(last_status).lower())
        if delay is None:
            return None

        max_attempts = self._recheck_max_attempts(schedule)
        if max_attempts and attempt_count >= max_attempts:
            return None

        return last_run + delay

    def _recheck_delay(self, schedule: ScheduleConfig, status: str) -> timedelta | None:
        """The recheck interval for a status: the monitor's own override if it
        set one, otherwise the settings.toml default.  None means no recheck."""
        if status not in RECHECK_STATUSES:
            return None

        override = getattr(schedule, f"recheck_after_{status}", None)
        if override is None:
            delay = self.recheck_delays.get(status)
        else:
            delay = self.parse_interval(override)
            if delay is None:
                logger.error(
                    f"Invalid recheck_after_{status} interval '{override}' - "
                    "no recheck will be scheduled"
                )
                return None

        # an unset default, or an explicit 0, turns the recheck off
        return delay or None

    def _recheck_max_attempts(self, schedule: ScheduleConfig) -> int:
        """The retry cap, 0 meaning unlimited.  A monitor setting 0 turns the
        cap off for itself rather than inheriting the settings.toml value."""
        if schedule.recheck_max_attempts is None:
            return max(self.recheck_max_attempts, 0)

        return max(schedule.recheck_max_attempts, 0)

    def _next_cron(
        self, schedule: ScheduleConfig, last_run: datetime | None, now: datetime
    ) -> datetime | None:
        base = last_run or (now - timedelta(seconds=1))
        try:
            iterator = croniter(schedule.cron or "", base)
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid cron expression '{schedule.cron}': {e}")
            return None

        return iterator.get_next(datetime)

    def _next_repeat(
        self, schedule: ScheduleConfig, last_run: datetime | None, now: datetime
    ) -> datetime | None:
        interval = self.parse_interval(schedule.repeat)
        if interval is None:
            logger.error(f"Invalid repeat interval '{schedule.repeat}'")
            return None

        if last_run is None:
            return self._window_start(schedule, now)

        return last_run + interval

    def _next_daily(
        self, schedule: ScheduleConfig, last_run: datetime | None, now: datetime
    ) -> datetime:
        start = schedule.start_time or time(0, 0)

        if last_run is None:
            return datetime.combine(now.date(), start)

        # already ran inside today's window - the next opportunity is tomorrow
        return datetime.combine(last_run.date() + timedelta(days=1), start)

    def _window_start(self, schedule: ScheduleConfig, moment: datetime) -> datetime:
        start = schedule.start_time
        if start is None:
            return moment

        return max(moment, datetime.combine(moment.date(), start))

    def _advance_into_window(
        self, schedule: ScheduleConfig, candidate: datetime, cron_days: bool = False
    ) -> datetime | None:
        """Push a candidate time forward until it lands on an allowed day and
        inside the start_time/end_time window.

        `cron_days` additionally holds the candidate to a date the monitor's
        cron expression would fire on - needed for a recheck, which is not
        itself a cron occurrence.
        """
        # with `cron` set, only start_time/end_time still apply - days_of_week
        # is already expressed in the cron expression itself
        allowed_days = (
            set() if schedule.cron else self.parse_days_of_week(schedule.days_of_week)
        )
        start = schedule.start_time
        end = schedule.end_time

        for _ in range(_MAX_WINDOW_DAYS):
            if allowed_days and candidate.weekday() not in allowed_days:
                candidate = self._start_of_next_day(candidate, start)
                continue

            if cron_days and not self._cron_allows_date(
                schedule.cron, candidate.date()
            ):
                candidate = self._start_of_next_day(candidate, start)
                continue

            if start is not None and end is not None and end < start:
                # overnight window (e.g. 22:00-06:00): valid whenever the
                # candidate is at/after start OR at/before end
                if candidate.time() >= start or candidate.time() <= end:
                    return candidate
                candidate = datetime.combine(candidate.date(), start)
                continue

            if start is not None and candidate.time() < start:
                candidate = datetime.combine(candidate.date(), start)
                continue

            if end is not None and candidate.time() > end:
                candidate = self._start_of_next_day(candidate, start)
                continue

            return candidate

        logger.error(
            "Unable to find a run time within a year for schedule "
            f"(days_of_week='{schedule.days_of_week}', start_time={start}, end_time={end})"
        )
        return None

    @staticmethod
    def _cron_allows_date(cron: str | None, day: date) -> bool:
        """True when the cron expression fires at some point on `day`.

        This covers day-of-week, day-of-month, and month in one go rather than
        just the weekday.  An unparseable expression allows the day - it is
        reported by `_next_cron` on the normal path, and a recheck should not
        be silently dropped on top of that.
        """
        if not cron:
            return True

        base = datetime.combine(day, time(0, 0)) - timedelta(microseconds=1)
        try:
            return croniter(cron, base).get_next(datetime).date() == day
        except (ValueError, KeyError):
            return True

    @staticmethod
    def _start_of_next_day(candidate: datetime, start: time | None) -> datetime:
        return datetime.combine(
            candidate.date() + timedelta(days=1), start or time(0, 0)
        )

    @staticmethod
    def parse_days_of_week(value: str | None) -> set[int]:
        """Parse `"S M Tu W Th F Sa"` into python weekday numbers (Mon=0)."""
        if not value:
            return set()

        days: set[int] = set()
        for token in re.split(r"[\s,]+", str(value).strip()):
            if not token:
                continue
            day = _DAY_TOKENS.get(token.lower())
            if day is None:
                logger.warning(f"Unknown day of week '{token}' - ignoring")
                continue
            days.add(day)

        return days

    @staticmethod
    def parse_interval(value: str | None) -> timedelta | None:
        """Parse `"5 min"`, `"30s"`, `"2 hours"` into a timedelta.

        Kept here as the scheduling entry point; the implementation lives in
        `helpers` because the notification throttle shares the syntax.
        """
        return parse_interval(value)
