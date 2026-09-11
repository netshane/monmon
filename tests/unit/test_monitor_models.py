import pytest

from locallib.monitor_models import ContactConfig, ScheduleConfig

"""
Test: monitor configuration dataclasses
"""


@pytest.mark.unit
def test_recheck_options_are_read_from_the_schedule_section():
    schedule = ScheduleConfig.from_dict(
        {
            "repeat": "5 min",
            "recheck_after_skipped": "15 min",
            "recheck_after_error": "1 hour",
            "recheck_after_alert": 30,
            "recheck_max_attempts": 3,
        }
    )

    assert schedule.recheck_after_skipped == "15 min"
    assert schedule.recheck_after_error == "1 hour"
    assert schedule.recheck_after_alert == "30"
    assert schedule.recheck_max_attempts == 3


@pytest.mark.unit
def test_unset_recheck_options_inherit_the_settings_default():
    schedule = ScheduleConfig.from_dict({"repeat": "5 min"})

    assert schedule.recheck_after_error is None
    assert schedule.recheck_max_attempts is None


@pytest.mark.unit
@pytest.mark.parametrize("value", ["three", "inf", ""])
def test_an_unreadable_recheck_max_attempts_is_ignored(value):
    schedule = ScheduleConfig.from_dict(
        {"repeat": "5 min", "recheck_max_attempts": value}
    )

    assert schedule.recheck_max_attempts is None


@pytest.mark.unit
def test_renotify_options_are_read_from_the_contact_section():
    contact = ContactConfig.from_dict(
        {
            "alert": ["email:ops@example.com"],
            "renotify_after_alert": "1 hour",
            "renotify_after_report": "12h",
            "renotify_after_error": 30,
        }
    )

    assert contact.renotify_after("alert") == "1 hour"
    assert contact.renotify_after("report") == "12h"
    # left as written for the gate to parse, so a bare number stays a string
    assert contact.renotify_after("error") == "30"


@pytest.mark.unit
def test_unset_renotify_options_inherit_the_settings_default():
    contact = ContactConfig.from_dict({"alert": ["email:ops@example.com"]})

    assert contact.renotify_after("alert") is None
    assert contact.renotify_after("notify") is None


@pytest.mark.unit
def test_an_explicit_zero_renotify_is_kept_as_an_override():
    """`0` must survive as a value - it turns the limit off for this monitor."""
    contact = ContactConfig.from_dict(
        {"alert": ["email:ops@example.com"], "renotify_after_alert": 0}
    )

    assert contact.renotify_after("alert") == "0"


@pytest.mark.unit
def test_renotify_after_rejects_an_unknown_contact_type():
    with pytest.raises(ValueError):
        ContactConfig().renotify_after("pager")
