from datetime import datetime, timedelta

import pytest

from locallib.contacts import CONTACT_TYPES, parse_contact_list
from locallib.email_sender import EmailSender, LoggingEmailSender, SendOutcome
from locallib.monitor_models import ContactConfig, MonitorDefinition
from locallib.monitor_results import (
    Alert,
    MonitorResult,
    Report,
    ResultStatus,
    TestResult,
)
from locallib.notifier import Notifier
from locallib.notify_throttle import NotifyThrottle, NotifyThrottleGate

"""
Test: the renotify_after_<type> rate limit on repeat notifications

The gate is exercised directly against a fake repository, then through a
Notifier so the combined-delivery and "only a live delivery counts" rules are
covered where they actually apply.
"""

NOW = datetime(2026, 8, 23, 12, 0, 0)


class FakeRepository:
    """Just the notification_state surface the gate uses."""

    def __init__(self):
        self.state: dict[tuple[str, str], dict] = {}
        self.cleared: list[tuple[str, list[str] | None]] = []

    def get_notification_state(self, monitor_name: str) -> dict[str, dict]:
        return {
            contact_type: dict(values)
            for (name, contact_type), values in self.state.items()
            if name == monitor_name
        }

    def record_notification(self, monitor_name, contact_type, sent_at, status=None):
        self.state[(monitor_name, contact_type)] = {
            "last_sent_at": sent_at,
            "last_status": status,
        }

    def clear_notification_state(self, monitor_name, contact_types=None):
        self.cleared.append((monitor_name, contact_types))
        for key in list(self.state):
            if key[0] == monitor_name and (
                contact_types is None or key[1] in contact_types
            ):
                del self.state[key]

        return 0


class FakeClock:
    def __init__(self, start: datetime = NOW):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs):
        self.now = self.now + timedelta(**kwargs)


class RecordingEmailSender(EmailSender):
    """A sender that reports as live, so deliveries start the window."""

    is_live = True

    def __init__(self, throttled: list[str] | None = None, fail: bool = False):
        self.throttled = throttled or []
        self.fail = fail
        self.sent: list[tuple[list[str], str]] = []

    def send(self, to, subject, body, html=None):
        if self.fail:
            raise RuntimeError("smtp is down")

        self.sent.append((list(to), subject))

        return SendOutcome(throttled=list(self.throttled))


@pytest.fixture()
def repository():
    return FakeRepository()


@pytest.fixture()
def clock():
    return FakeClock()


def _gate(repository, clock, **delays) -> NotifyThrottleGate:
    return NotifyThrottleGate(
        repository=repository,
        throttle=NotifyThrottle(
            delays={
                contact_type: timedelta(**value) if value else None
                for contact_type, value in delays.items()
            }
        ),
        clock=clock,
    )


# -- the gate ------------------------------------------------------------


@pytest.mark.unit
def test_first_notification_is_always_allowed(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})

    assert gate.allow("xyz", "alert", status="alert") is True


@pytest.mark.unit
def test_repeat_inside_the_window_is_blocked(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=30)

    assert gate.allow("xyz", "alert", status="alert") is False


@pytest.mark.unit
def test_repeat_after_the_window_is_allowed(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(hours=1)

    assert gate.allow("xyz", "alert", status="alert") is True


@pytest.mark.unit
def test_window_is_fixed_not_rolling(repository, clock):
    """A blocked attempt must not push the next one further out."""
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=45)
    assert gate.allow("xyz", "alert", status="alert") is False

    clock.advance(minutes=15)
    assert gate.allow("xyz", "alert", status="alert") is True


@pytest.mark.unit
def test_an_unthrottled_type_always_passes(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "report", "alert")

    assert gate.allow("xyz", "report", status="alert") is True


@pytest.mark.unit
def test_each_contact_type_has_its_own_window(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1}, error={"hours": 1})
    gate.record("xyz", "alert", "error")

    clock.advance(minutes=5)

    assert gate.allow("xyz", "alert", status="error") is False
    assert gate.allow("xyz", "error", status="error") is True


@pytest.mark.unit
def test_each_monitor_has_its_own_window(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=5)

    assert gate.allow("xyz", "alert", status="alert") is False
    assert gate.allow("abc", "alert", status="alert") is True


@pytest.mark.unit
def test_status_change_lets_an_alert_through(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=5)

    assert gate.allow("xyz", "alert", status="error") is True


@pytest.mark.unit
def test_status_change_does_not_lift_a_report_window(repository, clock):
    """report/info/notify fire on healthy runs, so status must not reset them."""
    gate = _gate(repository, clock, report={"hours": 1})
    gate.record("xyz", "report", "ok")

    clock.advance(minutes=5)

    assert gate.allow("xyz", "report", status="alert") is False


@pytest.mark.unit
def test_reset_clears_only_the_status_driven_types(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1}, report={"hours": 1})
    gate.record("xyz", "alert", "alert")
    gate.record("xyz", "report", "alert")

    gate.reset("xyz")
    clock.advance(minutes=5)

    assert gate.allow("xyz", "alert", status="alert") is True
    assert gate.allow("xyz", "report", status="alert") is False


@pytest.mark.unit
def test_monitor_override_beats_the_default(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=30)

    assert gate.allow("xyz", "alert", status="alert", override="15 min") is True


@pytest.mark.unit
def test_explicit_zero_turns_the_limit_off(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=1)

    assert gate.allow("xyz", "alert", status="alert", override="0") is True


@pytest.mark.unit
def test_an_unparseable_override_leaves_the_type_unthrottled(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=1)

    assert gate.allow("xyz", "alert", status="alert", override="soon") is True


@pytest.mark.unit
def test_a_bare_number_override_means_minutes(repository, clock):
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=5)
    assert gate.allow("xyz", "alert", status="alert", override="10") is False

    clock.advance(minutes=5)
    assert gate.allow("xyz", "alert", status="alert", override="10") is True


# -- through the notifier ------------------------------------------------


def _definition(name: str = "xyz", **contact) -> MonitorDefinition:
    lists = {
        key: parse_contact_list(value)
        for key, value in contact.items()
        if key in CONTACT_TYPES
    }
    rest = {k: v for k, v in contact.items() if k not in CONTACT_TYPES}

    return MonitorDefinition(
        name=name,
        source_file=f"{name}.toml",
        monitor_type_alert=True,
        monitor_type_report=True,
        contact=ContactConfig(**lists, **rest),
    )


def _alert_result(name: str = "xyz") -> MonitorResult:
    return MonitorResult(
        monitor_name=name,
        status=ResultStatus.ALERT,
        test_results=[
            TestResult(
                key=f"{name}.test",
                test_type="ping",
                status=ResultStatus.ALERT,
                alerts=[Alert(name="host1", message="host1 is down")],
            )
        ],
    )


def _report_result(name: str = "xyz") -> MonitorResult:
    return MonitorResult(
        monitor_name=name,
        status=ResultStatus.OK,
        test_results=[
            TestResult(
                key=f"{name}.report",
                test_type="dbreport",
                reports=[Report(title="rows", columns=["Name"], rows=[["a"]])],
            )
        ],
    )


def _notifier(sender, gate) -> Notifier:
    return Notifier(email_sender=sender, throttle_gate=gate)


@pytest.mark.unit
def test_notifier_suppresses_a_repeat_alert(repository, clock):
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())
    clock.advance(minutes=10)
    notifier.notify(definition, _alert_result())

    assert len(sender.sent) == 1


@pytest.mark.unit
def test_notifier_sends_again_once_the_window_passes(repository, clock):
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())
    clock.advance(hours=1)
    notifier.notify(definition, _alert_result())

    assert len(sender.sent) == 2


@pytest.mark.unit
def test_recovery_resets_the_window(repository, clock):
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())
    clock.advance(minutes=5)
    notifier.notify(
        definition, MonitorResult(monitor_name="xyz", status=ResultStatus.OK)
    )
    clock.advance(minutes=5)
    notifier.notify(definition, _alert_result())

    assert len(sender.sent) == 2


@pytest.mark.unit
def test_a_failed_delivery_does_not_start_the_window(repository, clock):
    """Otherwise an smtp outage silences the alerts that follow it."""
    sender = RecordingEmailSender(fail=True)
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())
    clock.advance(minutes=1)

    assert repository.state == {}


@pytest.mark.unit
def test_a_quota_throttled_delivery_does_not_start_the_window(repository, clock):
    sender = RecordingEmailSender(throttled=["ops@example.com"])
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())

    assert repository.state == {}


@pytest.mark.unit
def test_a_logged_only_delivery_does_not_start_the_window(repository, clock):
    """A channel with no credentials has not told anybody anything."""
    notifier = _notifier(
        LoggingEmailSender(), _gate(repository, clock, alert={"hours": 1})
    )
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())

    assert repository.state == {}


@pytest.mark.unit
def test_combined_alerts_are_suppressed_before_they_join_the_batch(repository, clock):
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    first = _definition("xyz", alert=["email:ops@example.com"])
    # abc opts down to a 5 minute window, so it is due again before xyz is
    second = _definition(
        "abc", alert=["email:ops@example.com"], renotify_after_alert="5 min"
    )

    notifier.notify(first, _alert_result("xyz"))
    notifier.notify(second, _alert_result("abc"))
    notifier.flush()

    clock.advance(minutes=10)

    notifier.notify(first, _alert_result("xyz"))
    notifier.notify(second, _alert_result("abc"))
    notifier.flush()

    assert len(sender.sent) == 2
    # the second batch is abc alone, so the subject never mentions xyz
    assert "abc" in sender.sent[1][1]
    assert "xyz" not in sender.sent[1][1]


@pytest.mark.unit
def test_a_combined_batch_records_every_contributing_monitor(repository, clock):
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    first = _definition("xyz", alert=["email:ops@example.com"])
    second = _definition("abc", alert=["email:ops@example.com"])

    notifier.notify(first, _alert_result("xyz"))
    notifier.notify(second, _alert_result("abc"))
    notifier.flush()

    assert ("xyz", "alert") in repository.state
    assert ("abc", "alert") in repository.state


@pytest.mark.unit
def test_reports_are_rate_limited_across_healthy_runs(repository, clock):
    """A report monitor runs ok every time, so only the window holds it back."""
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, report={"hours": 12}))
    definition = _definition(report=["email:ops@example.com"], combine_reports=False)

    notifier.notify(definition, _report_result())
    clock.advance(hours=1)
    notifier.notify(definition, _report_result())

    assert len(sender.sent) == 1


@pytest.mark.unit
def test_test_notify_bypasses_the_rate_limit(repository, clock):
    sender = RecordingEmailSender()
    notifier = _notifier(sender, _gate(repository, clock, alert={"hours": 1}))
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())
    clock.advance(minutes=1)
    notifier.send_test(definition, "alert")

    assert len(sender.sent) == 2


@pytest.mark.unit
def test_no_gate_leaves_every_notification_unthrottled(repository, clock):
    sender = RecordingEmailSender()
    notifier = Notifier(email_sender=sender)
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result())
    notifier.notify(definition, _alert_result())

    assert len(sender.sent) == 2


@pytest.mark.unit
def test_recovery_writes_nothing_when_no_type_is_rate_limited(repository, clock):
    """The shipped defaults are all zero - a healthy run must cost no write."""
    gate = _gate(repository, clock)
    notifier = _notifier(RecordingEmailSender(), gate)
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(
        definition, MonitorResult(monitor_name="xyz", status=ResultStatus.OK)
    )

    assert repository.cleared == []


@pytest.mark.unit
def test_recovery_still_clears_a_monitor_that_overrides_the_default(repository, clock):
    """A per monitor override must reset even with the global default off."""
    gate = _gate(repository, clock)
    notifier = _notifier(RecordingEmailSender(), gate)
    definition = _definition(
        alert=["email:ops@example.com"],
        combine_alerts=False,
        renotify_after_alert="1 hour",
    )

    notifier.notify(definition, _alert_result())
    clock.advance(minutes=5)
    notifier.notify(
        definition, MonitorResult(monitor_name="xyz", status=ResultStatus.OK)
    )
    clock.advance(minutes=5)
    notifier.notify(definition, _alert_result())

    assert repository.cleared == [("xyz", ["alert"])]


@pytest.mark.unit
def test_an_out_of_range_override_leaves_the_type_unthrottled(repository, clock):
    """A timedelta cannot hold it, and that must not take the run down."""
    gate = _gate(repository, clock, alert={"hours": 1})
    gate.record("xyz", "alert", "alert")

    clock.advance(minutes=1)

    assert gate.allow("xyz", "alert", status="alert", override="999999999999 d") is True
