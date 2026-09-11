from datetime import datetime, timedelta

import pytest

from locallib.email_quota import EmailQuota, EmailQuotaGate
from locallib.email_sender import (
    EmailSender,
    LoggingEmailSender,
    QuotaLimitedEmailSender,
    SendOutcome,
)

from .test_email_quota import NOW, FakeRepository

"""
Test: the quota limited email sender

The inner sender is a fake transport, so "no smtp connection is opened" is
observable as "the inner sender was never called".
"""


class FakeTransport(EmailSender):
    """Records what it was asked to send, and can be made to fail."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def send(self, to, subject, body, html=None):
        self.calls.append({"to": list(to), "subject": subject, "body": body})
        if self.error:
            raise self.error


def _sender(repository, transport=None, **quota):
    return QuotaLimitedEmailSender(
        inner=transport or FakeTransport(),
        gate=EmailQuotaGate(
            repository=repository, quota=EmailQuota(**quota), clock=lambda: NOW
        ),
    )


@pytest.mark.unit
def test_a_plain_sender_reports_no_outcome():
    sender = LoggingEmailSender()

    assert sender.send(["a@x.com"], "subject", "body") is None


@pytest.mark.unit
def test_under_the_limit_everything_is_sent_and_logged():
    repository = FakeRepository()
    transport = FakeTransport()
    sender = _sender(repository, transport, max_per_day=10, max_per_recipient=3)

    outcome = sender.send(["a@x.com", "b@x.com"], "down", "body")

    assert isinstance(outcome, SendOutcome)
    assert outcome.throttled == []
    assert transport.calls[0]["to"] == ["a@x.com", "b@x.com"]
    assert repository.recorded[0]["recipients"] == ["a@x.com", "b@x.com"]
    assert repository.recorded[0]["subject"] == "down"


@pytest.mark.unit
def test_nothing_is_sent_when_the_daily_quota_is_exhausted():
    repository = FakeRepository([("old@x.com", NOW - timedelta(hours=1))] * 5)
    transport = FakeTransport()
    sender = _sender(repository, transport, max_per_day=5)

    outcome = sender.send(["a@x.com", "b@x.com"], "down", "body")

    # no smtp connection is opened at all
    assert transport.calls == []
    assert repository.recorded == []
    assert set(outcome.throttled) == {"a@x.com", "b@x.com"}


@pytest.mark.unit
def test_the_recipients_under_the_limit_still_receive_the_message():
    repository = FakeRepository(
        [
            ("a@x.com", NOW - timedelta(minutes=5)),
            ("a@x.com", NOW - timedelta(minutes=15)),
        ]
    )
    transport = FakeTransport()
    sender = _sender(repository, transport, max_per_recipient=2)

    outcome = sender.send(["a@x.com", "b@x.com", "c@x.com"], "down", "body")

    assert transport.calls[0]["to"] == ["b@x.com", "c@x.com"]
    assert outcome.throttled == ["a@x.com"]
    assert repository.recorded[0]["recipients"] == ["b@x.com", "c@x.com"]


@pytest.mark.unit
def test_a_send_that_raises_does_not_consume_quota():
    repository = FakeRepository()
    transport = FakeTransport(error=RuntimeError("smtp is down"))
    sender = _sender(repository, transport, max_per_day=10)

    with pytest.raises(RuntimeError):
        sender.send(["a@x.com"], "down", "body")

    assert repository.recorded == []
    assert repository.count_email_sends_since(NOW - timedelta(hours=24)) == 0


@pytest.mark.unit
def test_is_live_follows_the_wrapped_sender():
    repository = FakeRepository()

    assert _sender(repository, FakeTransport(), max_per_day=1).is_live
    assert not _sender(repository, LoggingEmailSender(), max_per_day=1).is_live


@pytest.mark.unit
def test_sends_accumulate_across_calls():
    repository = FakeRepository()
    transport = FakeTransport()
    sender = _sender(repository, transport, max_per_day=3)

    sender.send(["a@x.com", "b@x.com"], "first", "body")
    outcome = sender.send(["c@x.com", "d@x.com"], "second", "body")

    assert transport.calls[1]["to"] == ["c@x.com"]
    assert outcome.throttled == ["d@x.com"]


@pytest.mark.unit
def test_the_send_log_records_the_time_from_the_injected_clock():
    repository = FakeRepository()
    sender = _sender(repository, max_per_day=5)

    sender.send(["a@x.com"], "down", "body")

    assert repository.recorded[0]["sent_at"] == NOW
    assert isinstance(repository.recorded[0]["sent_at"], datetime)


class FakeSettings:
    """The settings surface `get_email_sender` reads."""

    def __init__(self, **email):
        self.email = {
            "host": "smtp.example.com",
            "port": 587,
            "from_address": "monitors@example.com",
            **email,
        }
        self.passwords: dict = {}


def _built_sender(simulate: bool, **email) -> EmailSender:
    """`dependencies.get_email_sender` with settings and the db stubbed out."""
    from unittest.mock import patch

    import locallib.dependencies as dependencies

    with (
        patch.object(dependencies, "get_settings", return_value=FakeSettings(**email)),
        patch.object(
            dependencies, "get_monitor_repository", return_value=FakeRepository()
        ),
    ):
        return dependencies.get_email_sender(simulate=simulate)


@pytest.mark.unit
def test_the_retention_is_raised_to_cover_the_cooldown_window():
    from unittest.mock import patch

    import locallib.dependencies as dependencies

    settings = FakeSettings(
        max_per_recipient=1,
        cooldown_window_minutes=60 * 24 * 14,
        send_log_retention_days=7,
    )
    with patch.object(dependencies, "get_settings", return_value=settings):
        # purging inside the window would reset the cooldown
        assert dependencies.get_email_send_log_retention_days() == 14

    settings.email["send_log_retention_days"] = 30
    with patch.object(dependencies, "get_settings", return_value=settings):
        assert dependencies.get_email_send_log_retention_days() == 30


@pytest.mark.unit
def test_the_retention_is_left_alone_when_no_limit_is_configured():
    from unittest.mock import patch

    import locallib.dependencies as dependencies

    with patch.object(
        dependencies,
        "get_settings",
        return_value=FakeSettings(send_log_retention_days=1),
    ):
        assert dependencies.get_email_send_log_retention_days() == 1


@pytest.mark.unit
def test_a_live_sender_is_wrapped_when_a_limit_is_configured():
    sender = _built_sender(simulate=False, max_per_day=100)

    assert isinstance(sender, QuotaLimitedEmailSender)


@pytest.mark.unit
def test_a_live_sender_is_not_wrapped_when_no_limit_is_configured():
    sender = _built_sender(simulate=False)

    assert not isinstance(sender, QuotaLimitedEmailSender)


@pytest.mark.unit
def test_simulate_is_never_gated_so_it_writes_no_log_rows():
    # the easy bug in this feature: a simulated run must neither consume quota
    # nor write to the send log
    repository = FakeRepository()
    sender = _built_sender(simulate=True, max_per_day=100, max_per_recipient=1)

    assert not isinstance(sender, QuotaLimitedEmailSender)
    assert isinstance(sender, LoggingEmailSender)

    sender.send(["a@x.com"], "down", "body")
    sender.send(["a@x.com"], "down again", "body")

    assert repository.recorded == []
    assert repository.rows == []


@pytest.mark.unit
def test_a_send_log_failure_does_not_fail_a_delivered_message():
    class UnwritableRepository(FakeRepository):
        def record_email_sends(self, *args, **kwargs):
            raise RuntimeError("database is locked")

    repository = UnwritableRepository()
    transport = FakeTransport()
    sender = _sender(repository, transport, max_per_day=10)

    # the mail already left, so the caller must not hear about the log failure
    outcome = sender.send(["a@x.com"], "down", "body")

    assert transport.calls[0]["to"] == ["a@x.com"]
    assert outcome.throttled == []
