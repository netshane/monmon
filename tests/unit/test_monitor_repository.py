from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from locallib.monitor_repository import MonitorRepository
from locallib.monitor_results import (
    Alert,
    MonitorResult,
    Report,
    ResultStatus,
    TestResult,
)

"""
Test: monitor result persistence

Uses a sqlite file in tmp_path - the same code paths run against postgres.
"""


@pytest.fixture()
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'monitors.db'}", future=True)
    repository = MonitorRepository(engine=engine)
    repository.create_schema()

    return repository


def _result(
    name: str = "xyz",
    status: ResultStatus = ResultStatus.ALERT,
    started_at: datetime | None = None,
) -> MonitorResult:
    started_at = started_at or datetime(2026, 8, 12, 10, 0, 0)

    return MonitorResult(
        monitor_name=name,
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=2),
        message="1 alert(s)",
        test_results=[
            TestResult(
                key="net.ping",
                test_type="ping",
                status=ResultStatus.ALERT,
                message="down.com could not be reached",
                value=1,
                alerts=[
                    Alert(name="down.com", message="down.com could not be reached")
                ],
            ),
            TestResult(
                key="db.report",
                test_type="dbreport",
                status=ResultStatus.OK,
                reports=[Report(title="db.report", columns=["a"], rows=[[1]])],
            ),
        ],
    )


@pytest.mark.unit
def test_create_schema_is_repeatable(repository):
    repository.create_schema()

    assert repository.get_runs() == []


@pytest.mark.unit
def test_save_result_stores_the_run_and_its_tests(repository):
    result = _result()

    run_id = repository.save_result(result)

    runs = repository.get_runs()
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["monitor_name"] == "xyz"
    assert runs[0]["status"] == "alert"
    assert runs[0]["alert_count"] == 1
    assert runs[0]["report_count"] == 1
    assert result.run_id == run_id

    tests = repository.get_test_results(run_id)
    assert sorted(t["test_key"] for t in tests) == ["db.report", "net.ping"]
    assert (
        "down.com"
        in next(t for t in tests if t["test_key"] == "net.ping")["result_json"]
    )


@pytest.mark.unit
def test_get_test_results_needs_a_run_filter(repository):
    """Without a filter this would load every test result ever stored."""
    repository.save_result(_result())

    with pytest.raises(ValueError):
        repository.get_test_results()

    with pytest.raises(ValueError):
        repository.get_test_results(run_id=None)

    assert repository.get_test_results(run_ids=[]) == []


@pytest.mark.unit
def test_get_test_results_accepts_several_runs(repository):
    first = repository.save_result(_result(name="a"))
    second = repository.save_result(_result(name="b"))

    results = repository.get_test_results(run_ids=[first, second])

    assert {r["monitor_name"] for r in results} == {"a", "b"}


@pytest.mark.unit
def test_get_runs_filters_by_names_and_window(repository):
    base = datetime(2026, 8, 1, 10, 0, 0)
    repository.save_result(_result(name="a", started_at=base))
    repository.save_result(_result(name="b", started_at=base + timedelta(days=5)))
    repository.save_result(_result(name="c", started_at=base + timedelta(days=10)))

    windowed = repository.get_runs(
        monitor_names=["a", "b"], since=base + timedelta(days=1), limit=None
    )

    assert [r["monitor_name"] for r in windowed] == ["b"]
    assert repository.get_runs(monitor_names=[]) == []
    assert len(repository.get_runs(limit=None)) == 3
    assert repository.get_monitor_names() == ["a", "b", "c"]


@pytest.mark.unit
def test_save_result_updates_last_run_state(repository):
    first = datetime(2026, 8, 12, 10, 0, 0)
    repository.save_result(_result(status=ResultStatus.OK, started_at=first))
    repository.save_result(
        _result(status=ResultStatus.ALERT, started_at=first + timedelta(hours=1))
    )

    assert repository.get_last_status("xyz") == ResultStatus.ALERT
    assert repository.get_last_run("xyz") == first + timedelta(hours=1, seconds=2)
    assert len(repository.get_runs("xyz")) == 2


@pytest.mark.unit
def test_state_is_unknown_for_monitors_that_never_ran(repository):
    assert repository.get_state("nope") is None
    assert repository.get_last_run("nope") is None
    assert repository.get_last_status("nope") is None


@pytest.mark.unit
def test_set_next_run_upserts(repository):
    next_run = datetime(2026, 8, 12, 11, 0, 0)

    repository.set_next_run("xyz", next_run)
    repository.set_next_run("xyz", next_run + timedelta(minutes=5))

    state = repository.get_all_state()
    assert state["xyz"]["next_run_at"] == next_run + timedelta(minutes=5)
    assert state["xyz"]["last_run_at"] is None


@pytest.mark.unit
def test_set_next_run_keeps_last_run(repository):
    repository.save_result(_result())
    last_run = repository.get_last_run("xyz")

    repository.set_next_run("xyz", datetime(2026, 8, 12, 12, 0, 0))

    assert repository.get_last_run("xyz") == last_run


@pytest.mark.unit
def test_get_runs_is_limited_and_newest_first(repository):
    base = datetime(2026, 8, 12, 8, 0, 0)
    for hours in range(4):
        repository.save_result(_result(started_at=base + timedelta(hours=hours)))

    runs = repository.get_runs(limit=2)

    assert [r["started_at"] for r in runs] == [
        base + timedelta(hours=3),
        base + timedelta(hours=2),
    ]


@pytest.mark.unit
def test_purge_removes_old_runs_and_their_tests(repository):
    old = datetime(2026, 1, 1, 0, 0, 0)
    recent = datetime(2026, 8, 12, 0, 0, 0)
    old_id = repository.save_result(_result(started_at=old))
    repository.save_result(_result(started_at=recent))

    deleted = repository.purge_runs_before(datetime(2026, 6, 1))

    assert deleted == 1
    assert [r["started_at"] for r in repository.get_runs()] == [recent]
    assert repository.get_test_results(old_id) == []


@pytest.mark.unit
def test_purge_with_nothing_to_delete(repository):
    repository.save_result(_result())

    assert repository.purge_runs_before(datetime(2020, 1, 1)) == 0


@pytest.mark.unit
def test_purge_runs_before_clears_stale_state(repository):
    old = datetime(2026, 1, 1, 0, 0, 0)
    repository.save_result(_result(name="a", started_at=old))

    deleted = repository.purge_runs_before(datetime(2026, 6, 1))

    assert deleted == 1
    assert repository.get_state("a") is None


@pytest.mark.unit
def test_purge_runs_before_clears_state_for_a_deleted_run_that_finished_after_cutoff(
    repository,
):
    """A run that started before the cutoff but finished after it still gets
    deleted (started_at < cutoff); its state row must not be left pointing at
    the now-missing run."""
    started = datetime(2026, 1, 1, 0, 0, 0)
    run_id = repository.save_result(
        _result(name="a", started_at=started)
    )  # finishes 2 seconds later

    cutoff = started + timedelta(seconds=1)
    deleted = repository.purge_runs_before(cutoff)

    assert deleted == 1
    assert run_id  # sanity: a run was actually created
    assert repository.get_state("a") is None


@pytest.mark.unit
def test_purge_runs_before_can_be_limited_to_one_monitor(repository):
    old = datetime(2026, 1, 1, 0, 0, 0)
    repository.save_result(_result(name="a", started_at=old))
    repository.save_result(_result(name="b", started_at=old))

    deleted = repository.purge_runs_before(datetime(2026, 6, 1), monitor_name="a")

    assert deleted == 1
    assert repository.get_state("a") is None
    assert repository.get_state("b") is not None
    assert [r["monitor_name"] for r in repository.get_runs(limit=None)] == ["b"]


@pytest.mark.unit
def test_purge_monitor_removes_everything_for_that_monitor(repository):
    repository.save_result(_result(name="a"))
    repository.save_result(_result(name="b"))

    deleted = repository.purge_monitor("a")

    assert deleted == 1
    assert repository.get_state("a") is None
    assert repository.get_runs("a") == []
    assert repository.get_state("b") is not None
    assert [r["monitor_name"] for r in repository.get_runs(limit=None)] == ["b"]


@pytest.mark.unit
def test_purge_monitor_with_nothing_to_delete(repository):
    assert repository.purge_monitor("nope") == 0


@pytest.mark.unit
def test_email_sends_are_recorded_one_row_per_recipient(repository):
    sent_at = datetime(2026, 8, 22, 12, 0, 0)

    written = repository.record_email_sends(
        recipients=["a@x.com", "b@x.com"],
        monitor_name="xyz",
        subject="down",
        sent_at=sent_at,
    )

    assert written == 2
    assert repository.count_email_sends_since(sent_at) == 2
    assert repository.count_email_sends_since_by_recipient(
        sent_at, ["a@x.com", "b@x.com", "c@x.com"]
    ) == {"a@x.com": 1, "b@x.com": 1}


@pytest.mark.unit
def test_recording_no_recipients_writes_nothing(repository):
    assert repository.record_email_sends([]) == 0
    assert repository.count_email_sends_since(datetime(2000, 1, 1)) == 0


@pytest.mark.unit
def test_email_send_counts_honour_the_window(repository):
    now = datetime(2026, 8, 22, 12, 0, 0)
    repository.record_email_sends(["a@x.com"], sent_at=now - timedelta(hours=25))
    repository.record_email_sends(["a@x.com"], sent_at=now - timedelta(hours=24))
    repository.record_email_sends(["a@x.com"], sent_at=now - timedelta(minutes=5))

    cutoff = now - timedelta(hours=24)

    # the row exactly at the cutoff is inside the window
    assert repository.count_email_sends_since(cutoff) == 2
    assert repository.count_email_sends_since_by_recipient(cutoff, ["a@x.com"]) == {
        "a@x.com": 2
    }
    assert repository.count_email_sends_since_by_recipient(cutoff, []) == {}


@pytest.mark.unit
def test_the_email_send_log_is_purged_by_its_own_cutoff(repository):
    now = datetime(2026, 8, 22, 12, 0, 0)
    repository.record_email_sends(["old@x.com"], sent_at=now - timedelta(days=8))
    repository.record_email_sends(["new@x.com"], sent_at=now - timedelta(days=1))

    deleted = repository.purge_email_send_log_before(now - timedelta(days=7))

    assert deleted == 1
    assert repository.count_email_sends_since(datetime(2000, 1, 1)) == 1


"""
Test: the failure streak behind `recheck_max_attempts`
"""


def _streak_runs(repository, name: str, statuses: list[ResultStatus]):
    base = datetime(2026, 8, 12, 9, 0, 0)
    for offset, status in enumerate(statuses):
        repository.save_result(
            _result(
                name=name, status=status, started_at=base + timedelta(minutes=offset)
            )
        )


@pytest.mark.unit
def test_consecutive_failures_are_counted_since_the_last_ok(repository):
    _streak_runs(
        repository,
        "xyz",
        [
            ResultStatus.OK,
            ResultStatus.ERROR,
            ResultStatus.ERROR,
            ResultStatus.ALERT,
        ],
    )

    assert repository.consecutive_failure_counts() == {"xyz": 3}


@pytest.mark.unit
def test_a_monitor_that_recovered_has_no_failure_streak(repository):
    _streak_runs(repository, "xyz", [ResultStatus.ERROR, ResultStatus.OK])

    assert repository.consecutive_failure_counts() == {}


@pytest.mark.unit
def test_a_monitor_that_never_ran_ok_counts_every_run(repository):
    _streak_runs(repository, "xyz", [ResultStatus.SKIPPED, ResultStatus.SKIPPED])

    assert repository.consecutive_failure_counts() == {"xyz": 2}


@pytest.mark.unit
def test_failure_streaks_are_counted_per_monitor(repository):
    _streak_runs(repository, "xyz", [ResultStatus.OK, ResultStatus.ERROR])
    _streak_runs(repository, "abc", [ResultStatus.ALERT, ResultStatus.ALERT])
    _streak_runs(repository, "def", [ResultStatus.OK])

    assert repository.consecutive_failure_counts() == {"xyz": 1, "abc": 2}


@pytest.mark.unit
def test_no_runs_means_no_failure_streaks(repository):
    assert repository.consecutive_failure_counts() == {}


@pytest.mark.unit
def test_notification_state_round_trips(repository):
    sent_at = datetime(2026, 8, 23, 12, 0, 0)
    repository.record_notification("xyz", "alert", sent_at, status="alert")

    state = repository.get_notification_state("xyz")

    assert state["alert"]["last_sent_at"] == sent_at
    assert state["alert"]["last_status"] == "alert"


@pytest.mark.unit
def test_recording_a_notification_twice_updates_the_one_row(repository):
    first = datetime(2026, 8, 23, 12, 0, 0)
    repository.record_notification("xyz", "alert", first, status="alert")
    repository.record_notification(
        "xyz", "alert", first + timedelta(hours=1), status="error"
    )

    state = repository.get_notification_state("xyz")

    assert len(state) == 1
    assert state["alert"]["last_sent_at"] == first + timedelta(hours=1)
    assert state["alert"]["last_status"] == "error"


@pytest.mark.unit
def test_notification_state_is_kept_per_monitor_and_type(repository):
    sent_at = datetime(2026, 8, 23, 12, 0, 0)
    repository.record_notification("xyz", "alert", sent_at)
    repository.record_notification("xyz", "report", sent_at)
    repository.record_notification("abc", "alert", sent_at)

    assert set(repository.get_notification_state("xyz")) == {"alert", "report"}
    assert set(repository.get_notification_state("abc")) == {"alert"}


@pytest.mark.unit
def test_clearing_notification_state_can_be_limited_to_some_types(repository):
    sent_at = datetime(2026, 8, 23, 12, 0, 0)
    repository.record_notification("xyz", "alert", sent_at)
    repository.record_notification("xyz", "report", sent_at)

    repository.clear_notification_state("xyz", contact_types=["alert", "error"])

    assert set(repository.get_notification_state("xyz")) == {"report"}


@pytest.mark.unit
def test_clearing_notification_state_without_types_drops_them_all(repository):
    sent_at = datetime(2026, 8, 23, 12, 0, 0)
    repository.record_notification("xyz", "alert", sent_at)
    repository.record_notification("xyz", "report", sent_at)

    repository.clear_notification_state("xyz")

    assert repository.get_notification_state("xyz") == {}


@pytest.mark.unit
def test_purge_monitor_removes_its_notification_state(repository):
    repository.save_result(_result("xyz"))
    repository.record_notification("xyz", "alert", datetime(2026, 8, 23, 12, 0, 0))

    repository.purge_monitor("xyz")

    assert repository.get_notification_state("xyz") == {}


@pytest.mark.unit
def test_purge_keeps_a_timer_while_the_monitor_still_has_state(repository):
    """A window longer than the purge retention must not be truncated."""
    repository.save_result(_result("xyz", started_at=datetime(2026, 8, 23, 10, 0, 0)))
    repository.record_notification("xyz", "report", datetime(2026, 8, 1, 12, 0, 0))

    repository.purge_runs_before(datetime(2026, 8, 10, 0, 0, 0))

    assert set(repository.get_notification_state("xyz")) == {"report"}


@pytest.mark.unit
def test_purge_drops_timers_once_the_monitor_has_aged_out(repository):
    repository.save_result(_result("xyz", started_at=datetime(2026, 8, 1, 10, 0, 0)))
    repository.record_notification("xyz", "report", datetime(2026, 8, 1, 12, 0, 0))

    repository.purge_runs_before(datetime(2026, 8, 10, 0, 0, 0))

    assert repository.get_notification_state("xyz") == {}
