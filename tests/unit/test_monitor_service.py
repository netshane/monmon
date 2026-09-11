from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from locallib.email_sender import LoggingEmailSender
from locallib.monitor_loader import MonitorLoader
from locallib.monitor_repository import MonitorRepository
from locallib.monitor_results import Alert, ResultStatus, TestResult
from locallib.monitor_runner import MonitorRunner
from locallib.monitor_service import MonitorService
from locallib.notifier import Notifier
from locallib.schedule_calculator import ScheduleCalculator

"""
Test: the service the CLI drives - loading, scheduling, running, storing
"""

NOW = datetime(2026, 8, 12, 10, 30, 0)

PARENT = """
[settings]
name = "parent"
active = true

[schedule]
repeat = "5 min"

[hierarchy]
node_name = "group"

[contact]
alert = ["email:ops@example.com"]

[test.ping]
key = "parent.ping"
server = "parent.example.com"
"""

CHILD = """
[settings]
name = "child"
active = true

[schedule]
repeat = "5 min"

[hierarchy]
node_name = "server"
parent_node = "group"
skip_on_parent_fail = true

[test.ping]
key = "child.ping"
server = "child.example.com"
"""

ALERTING_CHILD = """
[settings]
name = "alerting_child"
active = true

[hierarchy]
node_name = "app"
parent_node = "group"
alert_on_parent_fail = true

[contact]
alert = ["email:ops@example.com"]

[test.ping]
key = "alerting_child.ping"
server = "app.example.com"
"""

INACTIVE = """
[settings]
name = "inactive"
active = false

[schedule]
repeat = "5 min"

[test.ping]
key = "inactive.ping"
server = "nope.example.com"
"""

MANUAL = """
[settings]
name = "manual"
active = true

[test.ping]
key = "manual.ping"
server = "manual.example.com"
"""


class StubTestFactory:
    """Every test passes unless its key is listed in `alerting`."""

    def __init__(self, alerting: set[str] | None = None):
        self.alerting = alerting or set()
        self.ran: list[str] = []

    def create(self, config):
        factory = self

        class _Test:
            def run(self):
                factory.ran.append(config.key)
                if config.key in factory.alerting:
                    return TestResult(
                        key=config.key,
                        test_type=config.test_type,
                        status=ResultStatus.ALERT,
                        alerts=[Alert(message=f"{config.key} failed")],
                    )
                return TestResult(key=config.key, test_type=config.test_type)

        return _Test()


class NoopConnectionFactory:
    def add_connections(self, new_connections: dict):
        pass

    def connections_snapshot(self) -> dict:
        return {}

    def restore_connections(self, snapshot: dict):
        pass


@pytest.fixture()
def monitors_dir(tmp_path):
    folder = tmp_path / "monitors"
    folder.mkdir()
    (folder / "parent.toml").write_text(PARENT)
    (folder / "child.toml").write_text(CHILD)
    (folder / "alerting_child.toml").write_text(ALERTING_CHILD)
    (folder / "inactive.toml").write_text(INACTIVE)
    (folder / "manual.toml").write_text(MANUAL)

    return folder


@pytest.fixture()
def build_service(monitors_dir, tmp_path):
    def build(alerting: set[str] | None = None, simulate: bool = False):
        repository = MonitorRepository(
            engine=create_engine(f"sqlite:///{tmp_path / 'results.db'}", future=True)
        )
        repository.create_schema()

        factory = StubTestFactory(alerting)
        sender = LoggingEmailSender()
        service = MonitorService(
            loader=MonitorLoader(str(monitors_dir)),
            runner=MonitorRunner(
                test_factory=factory,
                db_factory=NoopConnectionFactory(),
                docker_factory=NoopConnectionFactory(),
                opensearch_factory=NoopConnectionFactory(),
                simulate=simulate,
            ),
            repository=repository,
            schedule_calculator=ScheduleCalculator(now_provider=lambda: NOW),
            notifier=Notifier(email_sender=sender),
            persist_results=not simulate,
        )
        service.test_factory = factory
        service.email_sender = sender

        return service

    return build


@pytest.mark.unit
def test_monitors_are_loaded_once_and_can_be_looked_up(build_service):
    service = build_service()

    assert sorted(service.monitors()) == [
        "alerting_child",
        "child",
        "inactive",
        "manual",
        "parent",
    ]
    assert service.get_monitor("PARENT").name == "parent"

    with pytest.raises(KeyError):
        service.get_monitor("nope")


@pytest.mark.unit
def test_run_monitor_by_name_stores_the_result(build_service):
    service = build_service()

    result = service.run_monitor("parent")

    assert result.status == ResultStatus.OK
    assert service.test_factory.ran == ["parent.ping"]
    assert service.repository.get_last_status("parent") == ResultStatus.OK
    assert service.repository.get_last_run("parent") is not None


@pytest.mark.unit
def test_run_monitor_flushes_the_notifier(build_service):
    service = build_service(alerting={"parent.ping"})

    service.run_monitor("parent")

    assert len(service.email_sender.sent) == 1
    assert service.email_sender.sent[0].to == ["ops@example.com"]


@pytest.mark.unit
def test_inactive_monitors_are_not_run(build_service):
    service = build_service()

    result = service.run_monitor("inactive")

    assert result.status == ResultStatus.INACTIVE
    assert service.test_factory.ran == []
    assert service.repository.get_runs("inactive") == []


@pytest.mark.unit
def test_force_runs_an_inactive_monitor(build_service):
    service = build_service()

    result = service.run_monitor("inactive", force=True)

    assert result.status == ResultStatus.OK
    assert service.test_factory.ran == ["inactive.ping"]


@pytest.mark.unit
def test_schedule_entries_report_next_run_and_due(build_service):
    service = build_service()
    entries = {e.name: e for e in service.schedule_entries()}

    assert entries["parent"].due is True
    assert entries["parent"].next_run_at == NOW
    # no schedule settings at all - manual only
    assert entries["manual"].next_run_at is None
    assert entries["manual"].due is False
    # inactive monitors are never due
    assert entries["inactive"].due is False
    assert entries["inactive"].active is False


@pytest.mark.unit
def test_next_run_moves_forward_after_a_run(build_service):
    service = build_service()

    service.run_monitor("parent")
    entry = next(e for e in service.schedule_entries() if e.name == "parent")

    assert entry.due is False
    assert entry.next_run_at > NOW
    assert entry.last_status == "ok"


@pytest.mark.unit
def test_store_next_runs_persists_the_calculated_time(build_service):
    service = build_service()

    service.store_next_runs()

    state = service.repository.get_all_state()
    assert state["parent"]["next_run_at"] == NOW
    assert state["manual"]["next_run_at"] is None


@pytest.mark.unit
def test_run_scheduled_runs_everything_due(build_service):
    service = build_service()

    results = service.run_scheduled()

    assert sorted(r.monitor_name for r in results) == ["child", "parent"]
    assert all(r.status == ResultStatus.OK for r in results)


@pytest.mark.unit
def test_run_scheduled_runs_parents_before_children(build_service):
    service = build_service()

    results = service.run_scheduled()

    assert [r.monitor_name for r in results] == ["parent", "child"]


@pytest.mark.unit
def test_child_is_skipped_when_its_parent_failed(build_service):
    service = build_service(alerting={"parent.ping"})

    results = {r.monitor_name: r for r in service.run_scheduled()}

    assert results["parent"].status == ResultStatus.ALERT
    assert results["child"].status == ResultStatus.SKIPPED
    assert "parent 'group' failed" in results["child"].message
    assert "child.ping" not in service.test_factory.ran
    # the skip is recorded so history shows why nothing ran
    assert service.repository.get_last_status("child") == ResultStatus.SKIPPED


@pytest.mark.unit
def test_alert_on_parent_fail_adds_an_alert(build_service):
    service = build_service(alerting={"parent.ping"})

    service.run_monitor("parent")
    result = service.run_monitor("alerting_child")

    assert result.status == ResultStatus.ALERT
    assert any("Parent node 'group'" in a.message for a in result.alerts)
    # the monitor's own test still ran
    assert "alerting_child.ping" in service.test_factory.ran


@pytest.mark.unit
def test_run_scheduled_sends_combined_alert_email(build_service):
    service = build_service(alerting={"parent.ping"})

    service.run_scheduled()

    assert len(service.email_sender.sent) == 1
    assert "parent.ping failed" in service.email_sender.sent[0].body


@pytest.mark.unit
def test_run_monitors_sends_one_combined_email_not_one_per_monitor(build_service):
    service = build_service(alerting={"parent.ping", "alerting_child.ping"})

    service.run_monitors(["parent", "alerting_child"])

    assert len(service.email_sender.sent) == 1
    assert "parent.ping failed" in service.email_sender.sent[0].body
    assert "alerting_child.ping failed" in service.email_sender.sent[0].body


@pytest.mark.unit
def test_run_monitors_reports_an_unknown_name_without_aborting_the_others(
    build_service,
):
    service = build_service()

    results = {r.monitor_name: r for r in service.run_monitors(["parent", "nope"])}

    assert results["parent"].status == ResultStatus.OK
    assert service.test_factory.ran == ["parent.ping"]
    assert results["nope"].status == ResultStatus.ERROR
    assert "nope" in results["nope"].message


@pytest.mark.unit
def test_run_scheduled_does_nothing_when_nothing_is_due(build_service):
    service = build_service()
    service.run_scheduled()

    later = MonitorService(
        loader=service.loader,
        runner=service.runner,
        repository=service.repository,
        schedule_calculator=ScheduleCalculator(
            now_provider=lambda: NOW + timedelta(minutes=1)
        ),
        notifier=service.notifier,
    )

    assert later.run_scheduled() == []


@pytest.mark.unit
def test_simulate_neither_runs_tests_nor_stores_results(build_service):
    service = build_service(simulate=True)

    result = service.run_monitor("parent")

    assert result.status == ResultStatus.SKIPPED
    assert service.test_factory.ran == []
    assert service.repository.get_runs("parent") == []


@pytest.mark.unit
def test_validate_reports_hierarchy_problems(build_service, monitors_dir):
    (monitors_dir / "orphan.toml").write_text(
        '[settings]\nname = "orphan"\n\n[hierarchy]\nnode_name = "o"\nparent_node = "missing"\n'
    )
    service = build_service()

    warnings = service.validate()

    assert any("unknown parent node 'missing'" in w for w in warnings)


"""
Test: rechecks - the service feeds last status and attempt count to the
schedule calculator
"""


def _recheck_service(build_service, max_attempts: int = 0):
    service = build_service()
    service.schedule_calculator = ScheduleCalculator(
        now_provider=lambda: NOW,
        recheck_delays={"error": timedelta(hours=1)},
        recheck_max_attempts=max_attempts,
    )

    return service


def _record_errors(service, name: str, count: int):
    from locallib.monitor_results import MonitorResult

    for offset in range(count):
        started = NOW - timedelta(minutes=count - offset)
        service.repository.save_result(
            MonitorResult(
                monitor_name=name,
                status=ResultStatus.ERROR,
                started_at=started,
                finished_at=NOW - timedelta(minutes=10),
            )
        )


def _entry(service, name: str):
    return next(e for e in service.schedule_entries(NOW) if e.name == name)


@pytest.mark.unit
def test_an_errored_monitor_is_scheduled_on_its_recheck_delay(build_service):
    service = _recheck_service(build_service)
    _record_errors(service, "parent", 1)

    entry = _entry(service, "parent")

    # last run was 10 minutes ago: the 5 min cadence would already be due
    assert entry.last_status == "error"
    assert entry.next_run_at == NOW - timedelta(minutes=10) + timedelta(hours=1)
    assert entry.due is False


@pytest.mark.unit
def test_a_recovered_monitor_goes_back_on_its_normal_cadence(build_service):
    from locallib.monitor_results import MonitorResult

    service = _recheck_service(build_service)
    _record_errors(service, "parent", 1)
    service.repository.save_result(
        MonitorResult(
            monitor_name="parent",
            status=ResultStatus.OK,
            started_at=NOW - timedelta(minutes=10),
            finished_at=NOW - timedelta(minutes=10),
        )
    )

    entry = _entry(service, "parent")

    assert entry.next_run_at == NOW - timedelta(minutes=5)
    assert entry.due is True


@pytest.mark.unit
def test_the_attempt_cap_puts_a_monitor_back_on_its_normal_cadence(build_service):
    service = _recheck_service(build_service, max_attempts=2)

    # the run that first failed is not a retry, so two errors is one attempt
    _record_errors(service, "parent", 2)
    assert _entry(service, "parent").next_run_at == NOW - timedelta(
        minutes=10
    ) + timedelta(hours=1)

    # a third error uses the last attempt up
    _record_errors(service, "parent", 1)
    entry = _entry(service, "parent")
    assert entry.next_run_at == NOW - timedelta(minutes=5)
    assert entry.due is True
