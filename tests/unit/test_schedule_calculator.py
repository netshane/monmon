from datetime import datetime, time, timedelta

import pytest

from locallib.monitor_models import ScheduleConfig, parse_time_of_day
from locallib.schedule_calculator import ScheduleCalculator

"""
Test: schedule calculation
"""

# Wednesday
NOW = datetime(2026, 8, 12, 10, 30, 0)


@pytest.fixture()
def calculator():
    return ScheduleCalculator(now_provider=lambda: NOW)


@pytest.mark.unit
def test_no_schedule_settings_means_manual_only(calculator):
    schedule = ScheduleConfig()

    assert calculator.next_run(schedule, None, NOW) is None
    assert calculator.is_due(schedule, None, NOW) is False


@pytest.mark.unit
def test_daily_first_run_is_today_at_start_time(calculator):
    schedule = ScheduleConfig(daily=True, start_time=time(6, 0))

    assert calculator.next_run(schedule, None, NOW) == datetime(2026, 8, 12, 6, 0)


@pytest.mark.unit
def test_daily_after_a_run_moves_to_tomorrow(calculator):
    schedule = ScheduleConfig(daily=True, start_time=time(6, 0))
    last_run = datetime(2026, 8, 12, 6, 0, 5)

    assert calculator.next_run(schedule, last_run, NOW) == datetime(2026, 8, 13, 6, 0)


@pytest.mark.unit
def test_repeat_adds_the_interval_to_the_last_run(calculator):
    schedule = ScheduleConfig(repeat="5 min")
    last_run = datetime(2026, 8, 12, 10, 28, 0)

    assert calculator.next_run(schedule, last_run, NOW) == datetime(2026, 8, 12, 10, 33)
    assert calculator.is_due(schedule, last_run, NOW) is False


@pytest.mark.unit
def test_repeat_is_due_once_the_interval_has_passed(calculator):
    schedule = ScheduleConfig(repeat="5 min")
    last_run = NOW - timedelta(minutes=6)

    assert calculator.is_due(schedule, last_run, NOW) is True


@pytest.mark.unit
def test_repeat_with_no_history_runs_immediately(calculator):
    schedule = ScheduleConfig(repeat="5 min")

    assert calculator.next_run(schedule, None, NOW) == NOW
    assert calculator.is_due(schedule, None, NOW) is True


@pytest.mark.unit
def test_cron_overrides_daily_and_repeat(calculator):
    schedule = ScheduleConfig(cron="0 * * * *", daily=True, repeat="5 min")

    assert calculator.next_run(schedule, None, NOW) == datetime(2026, 8, 12, 11, 0)


@pytest.mark.unit
def test_invalid_cron_is_reported_as_unscheduled(calculator):
    schedule = ScheduleConfig(cron="xxxxx")

    assert calculator.next_run(schedule, None, NOW) is None


@pytest.mark.unit
def test_start_and_end_time_still_apply_to_cron(calculator):
    # every hour, but only between 08:00 and 09:00
    schedule = ScheduleConfig(
        cron="0 * * * *", start_time=time(8, 0), end_time=time(9, 0)
    )

    assert calculator.next_run(schedule, None, NOW) == datetime(2026, 8, 13, 8, 0)


@pytest.mark.unit
def test_time_before_the_window_is_pushed_to_the_window_start(calculator):
    schedule = ScheduleConfig(repeat="5 min", start_time=time(14, 0))
    last_run = datetime(2026, 8, 12, 9, 0)

    assert calculator.next_run(schedule, last_run, NOW) == datetime(2026, 8, 12, 14, 0)


@pytest.mark.unit
def test_days_of_week_skips_disallowed_days(calculator):
    # Wednesday is not allowed, so the next run lands on Thursday
    schedule = ScheduleConfig(daily=True, days_of_week="Th F", start_time=time(6, 0))

    assert calculator.next_run(schedule, None, NOW) == datetime(2026, 8, 13, 6, 0)


@pytest.mark.unit
def test_days_of_week_parsing():
    assert ScheduleCalculator.parse_days_of_week("S M Tu W Th F Sa") == {
        0,
        1,
        2,
        3,
        4,
        5,
        6,
    }
    assert ScheduleCalculator.parse_days_of_week("Mon,Fri") == {0, 4}
    assert ScheduleCalculator.parse_days_of_week("") == set()
    assert ScheduleCalculator.parse_days_of_week("nonsense") == set()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected",
    [
        ("5 min", timedelta(minutes=5)),
        ("30s", timedelta(seconds=30)),
        ("2 hours", timedelta(hours=2)),
        ("1 day", timedelta(days=1)),
        ("10", timedelta(minutes=10)),
        ("bad", None),
        # too large for a timedelta - unusable, but it must not raise
        ("999999999999 days", None),
    ],
)
def test_interval_parsing(text, expected):
    assert ScheduleCalculator.parse_interval(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected",
    [
        ("06:00", time(6, 0)),
        ("6:05:30", time(6, 5, 30)),
        ("0600", time(6, 0)),
        ("", None),
        (None, None),
    ],
)
def test_time_of_day_parsing(text, expected):
    assert parse_time_of_day(text) == expected


@pytest.mark.unit
def test_unparseable_time_of_day_raises():
    with pytest.raises(ValueError):
        parse_time_of_day("half past six")


"""
Test: rechecks - a monitor left in a non-ok status comes off its normal cadence
"""

LAST_RUN = datetime(2026, 8, 12, 10, 0, 0)


@pytest.fixture()
def recheck_calculator():
    return ScheduleCalculator(
        now_provider=lambda: NOW,
        recheck_delays={
            "skipped": timedelta(minutes=15),
            "error": timedelta(hours=1),
            "alert": timedelta(minutes=30),
        },
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "status,expected",
    [
        ("skipped", datetime(2026, 8, 12, 10, 15)),
        ("error", datetime(2026, 8, 12, 11, 0)),
        ("alert", datetime(2026, 8, 12, 10, 30)),
    ],
)
def test_recheck_replaces_the_normal_cadence(recheck_calculator, status, expected):
    schedule = ScheduleConfig(repeat="5 min")

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status=status)

    assert next_run == expected


@pytest.mark.unit
def test_ok_status_keeps_the_normal_cadence(recheck_calculator):
    schedule = ScheduleConfig(repeat="5 min")

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="ok")

    assert next_run == datetime(2026, 8, 12, 10, 5)


@pytest.mark.unit
def test_no_configured_delay_keeps_the_normal_cadence(calculator):
    schedule = ScheduleConfig(repeat="5 min")

    next_run = calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")

    assert next_run == datetime(2026, 8, 12, 10, 5)


@pytest.mark.unit
def test_monitor_override_beats_the_settings_default(recheck_calculator):
    schedule = ScheduleConfig(repeat="5 min", recheck_after_error="10 min")

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")

    assert next_run == datetime(2026, 8, 12, 10, 10)


@pytest.mark.unit
def test_monitor_zero_turns_off_an_inherited_recheck(recheck_calculator):
    schedule = ScheduleConfig(repeat="5 min", recheck_after_error="0")

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")

    assert next_run == datetime(2026, 8, 12, 10, 5)


@pytest.mark.unit
def test_unparseable_recheck_interval_falls_back_to_the_normal_cadence(
    recheck_calculator,
):
    schedule = ScheduleConfig(repeat="5 min", recheck_after_error="whenever")

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")

    assert next_run == datetime(2026, 8, 12, 10, 5)


@pytest.mark.unit
def test_a_bare_recheck_number_is_minutes(recheck_calculator):
    schedule = ScheduleConfig(repeat="5 min", recheck_after_error="90")

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")

    assert next_run == datetime(2026, 8, 12, 11, 30)


@pytest.mark.unit
def test_a_recheck_is_pushed_into_the_time_window(recheck_calculator):
    schedule = ScheduleConfig(
        repeat="5 min",
        start_time=parse_time_of_day("14:00"),
        end_time=parse_time_of_day("18:00"),
    )

    next_run = recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")

    assert next_run == datetime(2026, 8, 12, 14, 0)


@pytest.mark.unit
def test_a_manual_monitor_never_rechecks(recheck_calculator):
    schedule = ScheduleConfig()

    assert (
        recheck_calculator.next_run(schedule, LAST_RUN, NOW, last_status="error")
        is None
    )


@pytest.mark.unit
def test_a_never_run_monitor_never_rechecks(recheck_calculator):
    schedule = ScheduleConfig(repeat="5 min")

    next_run = recheck_calculator.next_run(schedule, None, NOW, last_status="error")

    assert next_run == NOW


@pytest.mark.unit
def test_recheck_stops_once_max_attempts_is_reached():
    calculator = ScheduleCalculator(
        now_provider=lambda: NOW,
        recheck_delays={"error": timedelta(hours=1)},
        recheck_max_attempts=3,
    )
    schedule = ScheduleConfig(repeat="5 min")

    # three rechecks after the run that first failed, then back on the cadence
    assert calculator.next_run(
        schedule, LAST_RUN, NOW, last_status="error", attempt_count=2
    ) == datetime(2026, 8, 12, 11, 0)
    assert calculator.next_run(
        schedule, LAST_RUN, NOW, last_status="error", attempt_count=3
    ) == datetime(2026, 8, 12, 10, 5)


@pytest.mark.unit
def test_a_monitor_can_opt_out_of_the_attempt_cap():
    calculator = ScheduleCalculator(
        now_provider=lambda: NOW,
        recheck_delays={"error": timedelta(hours=1)},
        recheck_max_attempts=3,
    )
    schedule = ScheduleConfig(repeat="5 min", recheck_max_attempts=0)

    next_run = calculator.next_run(
        schedule, LAST_RUN, NOW, last_status="error", attempt_count=99
    )

    assert next_run == datetime(2026, 8, 12, 11, 0)


@pytest.mark.unit
def test_rechecks_are_not_possible_when_nothing_is_configured(calculator):
    assert calculator.rechecks_possible([ScheduleConfig(repeat="5 min")]) is False


@pytest.mark.unit
def test_a_settings_default_makes_rechecks_possible(recheck_calculator):
    assert (
        recheck_calculator.rechecks_possible([ScheduleConfig(repeat="5 min")]) is True
    )


@pytest.mark.unit
def test_a_monitor_override_alone_makes_rechecks_possible(calculator):
    schedules = [
        ScheduleConfig(repeat="5 min"),
        ScheduleConfig(repeat="5 min", recheck_after_alert="10 min"),
    ]

    assert calculator.rechecks_possible(schedules) is True


"""
Test: a recheck is not a cron occurrence, so it needs the cron's own day
restriction applied to it separately
"""

# Friday
CRON_LAST_RUN = datetime(2026, 8, 14, 8, 0, 0)


@pytest.fixture()
def daily_recheck_calculator():
    return ScheduleCalculator(
        now_provider=lambda: NOW,
        recheck_delays={"error": timedelta(days=1), "alert": timedelta(hours=2)},
    )


@pytest.mark.unit
def test_a_recheck_skips_a_day_the_cron_excludes(daily_recheck_calculator):
    schedule = ScheduleConfig(cron="0 8 * * 1-5")

    next_run = daily_recheck_calculator.next_run(
        schedule, CRON_LAST_RUN, CRON_LAST_RUN, last_status="error"
    )

    # a day after Friday is Saturday, which "1-5" excludes - hold until Monday
    assert next_run == datetime(2026, 8, 17, 0, 0)


@pytest.mark.unit
def test_a_recheck_on_an_allowed_cron_day_keeps_its_own_time(
    daily_recheck_calculator,
):
    schedule = ScheduleConfig(cron="0 8 * * 1-5")

    next_run = daily_recheck_calculator.next_run(
        schedule, CRON_LAST_RUN, CRON_LAST_RUN, last_status="alert"
    )

    # still Friday, so the recheck runs off cadence at 10:00 rather than 08:00
    assert next_run == datetime(2026, 8, 14, 10, 0)


@pytest.mark.unit
def test_a_recheck_honours_a_day_of_month_cron(daily_recheck_calculator):
    schedule = ScheduleConfig(cron="0 8 1 * *")
    last_run = datetime(2026, 8, 1, 8, 0)

    next_run = daily_recheck_calculator.next_run(
        schedule, last_run, last_run, last_status="error"
    )

    assert next_run == datetime(2026, 9, 1, 0, 0)


@pytest.mark.unit
def test_an_everyday_cron_lets_a_recheck_through(daily_recheck_calculator):
    schedule = ScheduleConfig(cron="0 8 * * *")

    next_run = daily_recheck_calculator.next_run(
        schedule, CRON_LAST_RUN, CRON_LAST_RUN, last_status="error"
    )

    assert next_run == datetime(2026, 8, 15, 8, 0)


@pytest.mark.unit
def test_a_cron_recheck_pushed_to_a_new_day_starts_at_start_time(
    daily_recheck_calculator,
):
    schedule = ScheduleConfig(cron="0 8 * * 1-5", start_time=parse_time_of_day("09:00"))

    next_run = daily_recheck_calculator.next_run(
        schedule, CRON_LAST_RUN, CRON_LAST_RUN, last_status="error"
    )

    assert next_run == datetime(2026, 8, 17, 9, 0)


@pytest.mark.unit
def test_an_unparseable_cron_does_not_drop_the_recheck(daily_recheck_calculator):
    schedule = ScheduleConfig(cron="not a cron")

    next_run = daily_recheck_calculator.next_run(
        schedule, CRON_LAST_RUN, CRON_LAST_RUN, last_status="error"
    )

    assert next_run == datetime(2026, 8, 15, 8, 0)


@pytest.mark.unit
def test_the_cron_day_check_does_not_touch_a_normal_cron_run(
    daily_recheck_calculator,
):
    schedule = ScheduleConfig(cron="0 8 * * 1-5")

    next_run = daily_recheck_calculator.next_run(
        schedule, CRON_LAST_RUN, CRON_LAST_RUN, last_status="ok"
    )

    assert next_run == datetime(2026, 8, 17, 8, 0)
