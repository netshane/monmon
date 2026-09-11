from datetime import datetime, timedelta

import pytest

from locallib.email_quota import EmailQuota, EmailQuotaGate

"""
Test: the cross run email send quota

A fake repository stands in for `email_send_log`, and the clock is injected,
so the rolling windows are exercised without a database or any sleeping.
"""


class FakeRepository:
    """Just the send log surface of MonitorRepository."""

    def __init__(self, rows: list[tuple[str, datetime]] | None = None):
        # (recipient, sent_at)
        self.rows: list[tuple[str, datetime]] = list(rows or [])
        self.recorded: list[dict] = []

    def record_email_sends(
        self, recipients, monitor_name=None, subject=None, sent_at=None
    ):
        sent_at = sent_at or datetime.now()
        self.recorded.append(
            {
                "recipients": list(recipients),
                "monitor_name": monitor_name,
                "subject": subject,
                "sent_at": sent_at,
            }
        )
        self.rows.extend((recipient, sent_at) for recipient in recipients)

        return len(recipients)

    def count_email_sends_since(self, cutoff):
        return len([row for row in self.rows if row[1] >= cutoff])

    def count_email_sends_since_by_recipient(self, cutoff, recipients):
        counts: dict[str, int] = {}
        for recipient, sent_at in self.rows:
            if sent_at >= cutoff and recipient in recipients:
                counts[recipient] = counts.get(recipient, 0) + 1

        return counts


NOW = datetime(2026, 8, 22, 12, 0, 0)


def _gate(repository: FakeRepository, **quota) -> EmailQuotaGate:
    return EmailQuotaGate(
        repository=repository, quota=EmailQuota(**quota), clock=lambda: NOW
    )


@pytest.mark.unit
def test_quota_is_disabled_when_no_limit_is_set():
    assert not EmailQuota().enabled
    assert not EmailQuota(cooldown_window_minutes=30).enabled
    assert EmailQuota(max_per_day=10).enabled
    assert EmailQuota(max_per_recipient=2).enabled


@pytest.mark.unit
def test_a_disabled_quota_allows_everything():
    repository = FakeRepository([("a@x.com", NOW) for _ in range(100)])
    gate = _gate(repository)

    allowed, blocked = gate.allow(["a@x.com", "b@x.com"])

    assert allowed == ["a@x.com", "b@x.com"]
    assert blocked == {}


@pytest.mark.unit
def test_under_the_limit_everything_is_allowed_and_recorded():
    repository = FakeRepository()
    gate = _gate(repository, max_per_day=10, max_per_recipient=3)

    allowed, blocked = gate.allow(["a@x.com", "b@x.com"], subject="down")
    gate.record(allowed, subject="down")

    assert allowed == ["a@x.com", "b@x.com"]
    assert blocked == {}
    assert repository.recorded == [
        {
            "recipients": ["a@x.com", "b@x.com"],
            "monitor_name": None,
            "subject": "down",
            "sent_at": NOW,
        }
    ]


@pytest.mark.unit
def test_the_daily_quota_blocks_every_recipient_when_exhausted():
    repository = FakeRepository([("old@x.com", NOW - timedelta(hours=3))] * 5)
    gate = _gate(repository, max_per_day=5)

    allowed, blocked = gate.allow(["a@x.com", "b@x.com"])

    assert allowed == []
    assert set(blocked) == {"a@x.com", "b@x.com"}
    assert all("daily quota" in reason for reason in blocked.values())


@pytest.mark.unit
def test_the_daily_quota_allows_as_many_recipients_as_remain():
    repository = FakeRepository([("old@x.com", NOW - timedelta(hours=3))] * 4)
    gate = _gate(repository, max_per_day=5)

    allowed, blocked = gate.allow(["a@x.com", "b@x.com", "c@x.com"])

    assert allowed == ["a@x.com"]
    assert set(blocked) == {"b@x.com", "c@x.com"}


@pytest.mark.unit
def test_sends_outside_the_daily_window_do_not_count():
    repository = FakeRepository([("old@x.com", NOW - timedelta(hours=25))] * 9)
    gate = _gate(repository, max_per_day=5)

    allowed, blocked = gate.allow(["a@x.com"])

    assert allowed == ["a@x.com"]
    assert blocked == {}


@pytest.mark.unit
def test_a_row_exactly_at_the_daily_cutoff_still_counts():
    repository = FakeRepository([("old@x.com", NOW - timedelta(hours=24))])
    gate = _gate(repository, max_per_day=1)

    allowed, blocked = gate.allow(["a@x.com"])

    assert allowed == []
    assert set(blocked) == {"a@x.com"}


@pytest.mark.unit
def test_the_cooldown_blocks_only_the_recipients_over_it():
    repository = FakeRepository(
        [
            ("a@x.com", NOW - timedelta(minutes=10)),
            ("a@x.com", NOW - timedelta(minutes=5)),
            ("b@x.com", NOW - timedelta(minutes=5)),
        ]
    )
    gate = _gate(repository, max_per_recipient=2, cooldown_window_minutes=60)

    allowed, blocked = gate.allow(["a@x.com", "b@x.com", "c@x.com"])

    assert allowed == ["b@x.com", "c@x.com"]
    assert "per recipient cooldown" in blocked["a@x.com"]


@pytest.mark.unit
def test_a_row_exactly_at_the_cooldown_cutoff_still_counts():
    repository = FakeRepository([("a@x.com", NOW - timedelta(minutes=60))])
    gate = _gate(repository, max_per_recipient=1, cooldown_window_minutes=60)

    allowed, blocked = gate.allow(["a@x.com"])

    assert allowed == []
    assert set(blocked) == {"a@x.com"}


@pytest.mark.unit
def test_a_send_older_than_the_cooldown_window_is_forgotten():
    repository = FakeRepository([("a@x.com", NOW - timedelta(minutes=61))])
    gate = _gate(repository, max_per_recipient=1, cooldown_window_minutes=60)

    allowed, blocked = gate.allow(["a@x.com"])

    assert allowed == ["a@x.com"]
    assert blocked == {}


@pytest.mark.unit
def test_both_limits_apply_together():
    repository = FakeRepository(
        [("a@x.com", NOW - timedelta(minutes=5))]
        + [("old@x.com", NOW - timedelta(hours=1))] * 4
    )
    gate = _gate(
        repository, max_per_day=6, max_per_recipient=1, cooldown_window_minutes=60
    )

    allowed, blocked = gate.allow(["a@x.com", "b@x.com", "c@x.com"])

    # a is over its cooldown, and only one of the daily quota is left for the
    # two that survive it
    assert allowed == ["b@x.com"]
    assert "cooldown" in blocked["a@x.com"]
    assert "daily quota" in blocked["c@x.com"]


@pytest.mark.unit
def test_a_repeated_address_is_only_counted_once():
    repository = FakeRepository()
    gate = _gate(repository, max_per_day=2)

    allowed, blocked = gate.allow(["a@x.com", "a@x.com", "b@x.com"])

    assert allowed == ["a@x.com", "b@x.com"]
    assert blocked == {}


@pytest.mark.unit
def test_recording_nothing_writes_nothing():
    repository = FakeRepository()
    gate = _gate(repository, max_per_day=5)

    gate.record([])

    assert repository.recorded == []


@pytest.mark.unit
def test_a_named_contact_shares_the_count_of_its_bare_mailbox():
    # the same mailbox addressed with and without a display name is one
    # recipient as far as the relay - and so this quota - is concerned
    repository = FakeRepository([("a@x.com", NOW - timedelta(minutes=5))])
    gate = _gate(repository, max_per_recipient=1)

    allowed, blocked = gate.allow(["Ops Team <a@x.com>"])

    assert allowed == []
    assert set(blocked) == {"Ops Team <a@x.com>"}


@pytest.mark.unit
def test_two_forms_of_one_mailbox_are_de_duplicated():
    repository = FakeRepository()
    gate = _gate(repository, max_per_day=10)

    allowed, blocked = gate.allow(["Ops Team <a@x.com>", "a@x.com", "b@x.com"])

    assert allowed == ["Ops Team <a@x.com>", "b@x.com"]
    assert blocked == {}


@pytest.mark.unit
def test_the_send_log_stores_the_bare_mailbox():
    repository = FakeRepository()
    gate = _gate(repository, max_per_day=10)

    gate.record(["Ops Team <a@x.com>"], subject="down")

    assert repository.recorded[0]["recipients"] == ["a@x.com"]
    # so the next send sees the count whichever form addresses it
    assert gate.allow(["a@x.com"])[0] == ["a@x.com"]
