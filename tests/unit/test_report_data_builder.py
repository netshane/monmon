from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from locallib.monitor_repository import MonitorRepository
from locallib.monitor_results import (
    Alert,
    MonitorResult,
    ResultStatus,
    TestResult,
)
from locallib.report_data_builder import ReportDataBuilder
from locallib.report_models import FieldSpec, ReportConfig

"""
Test: report data resolution

Runs against a sqlite file in tmp_path through MonitorRepository - the same
code paths run against postgres.
"""

NOW = datetime(2026, 8, 12, 12, 0, 0)


@pytest.fixture()
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'monitors.db'}", future=True)
    repository = MonitorRepository(engine=engine)
    repository.create_schema()

    return repository


def _save_run(
    repository: MonitorRepository,
    name: str,
    days_ago: float,
    status: ResultStatus = ResultStatus.OK,
    duration: float = 1.0,
    error: str | None = None,
    alerts: list[Alert] | None = None,
):
    started_at = NOW - timedelta(days=days_ago)

    result = MonitorResult(
        monitor_name=name,
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=duration),
        test_results=[
            TestResult(
                key=f"{name}.ping",
                test_type="ping",
                status=status,
                value=duration * 100,
                error=error,
                alerts=alerts or [],
            )
        ],
    )

    return repository.save_result(result)


def _config(monitors: list[dict], **kwargs) -> ReportConfig:
    return ReportConfig.from_dict(
        "default", "/tmp/default", {"title": "T", "monitors": monitors, **kwargs}
    )


def _spec(name: str, data: dict) -> FieldSpec:
    return FieldSpec.from_dict(name, data)


@pytest.mark.unit
def test_latest_mode_returns_only_the_most_recent_value(repository):
    _save_run(repository, "backup-db", days_ago=3, status=ResultStatus.OK)
    _save_run(repository, "backup-db", days_ago=1, status=ResultStatus.ALERT)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config([{"match": "backup-*", "fields": {"status": {"mode": "latest"}}}]),
        now=NOW,
    )

    status = context["monitors"][0]["fields"]["status"]
    assert status["mode"] == "latest"
    assert status["value"] == "alert"
    assert status["count"] == 1
    assert status["at"] == NOW - timedelta(days=1)


@pytest.mark.unit
def test_range_mode_returns_a_list_scoped_to_the_window(repository):
    for days_ago, duration in ((40, 1.0), (5, 2.0), (1, 3.0)):
        _save_run(repository, "backup-db", days_ago=days_ago, duration=duration)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [
                {
                    "match": "backup-db",
                    "fields": {"run_time": {"mode": "range", "last": "30d"}},
                }
            ]
        ),
        now=NOW,
    )

    run_time = context["monitors"][0]["fields"]["run_time"]
    assert run_time["mode"] == "range"
    # the 40 day old run falls outside the window
    assert run_time["count"] == 2
    assert run_time["values"] == [2.0, 3.0]
    assert run_time["min"] == 2.0
    assert run_time["max"] == 3.0
    assert run_time["avg"] == 2.5


@pytest.mark.unit
def test_range_mode_honours_since(repository):
    _save_run(repository, "backup-db", days_ago=60)
    _save_run(repository, "backup-db", days_ago=2)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [
                {
                    "match": "backup-db",
                    "fields": {
                        "run_time": {"mode": "range", "since": "2026-08-01"},
                    },
                }
            ]
        ),
        now=NOW,
    )

    assert context["monitors"][0]["fields"]["run_time"]["count"] == 1


@pytest.mark.unit
def test_modes_can_be_mixed_within_one_monitor(repository):
    _save_run(repository, "backup-db", days_ago=2, duration=1.5)
    _save_run(repository, "backup-db", days_ago=1, duration=2.5)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [
                {
                    "match": "backup-db",
                    "fields": {
                        "status": {"mode": "latest"},
                        "run_time": {"mode": "range", "last": "30d"},
                    },
                }
            ]
        ),
        now=NOW,
    )

    fields = context["monitors"][0]["fields"]
    assert fields["status"]["value"] == "ok"
    assert fields["run_time"]["values"] == [1.5, 2.5]


@pytest.mark.unit
def test_test_values_are_addressable_by_key(repository):
    _save_run(repository, "backup-db", days_ago=1, duration=2.0)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [
                {
                    "match": "backup-db",
                    "fields": {"backup-db.ping": {"mode": "latest"}},
                }
            ]
        ),
        now=NOW,
    )

    assert context["monitors"][0]["fields"]["backup-db.ping"]["value"] == "200.0"


@pytest.mark.unit
def test_stats_errors_and_alerts_are_collected(repository):
    _save_run(repository, "backup-db", days_ago=3, status=ResultStatus.OK)
    _save_run(
        repository,
        "backup-db",
        days_ago=2,
        status=ResultStatus.ERROR,
        error="connection refused",
    )
    _save_run(
        repository,
        "backup-db",
        days_ago=1,
        status=ResultStatus.ALERT,
        alerts=[Alert(name="disk", message="disk is full", value=99, threshold=90)],
    )

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config([{"match": "backup-db", "fields": {"status": {"mode": "latest"}}}]),
        now=NOW,
    )

    monitor = context["monitors"][0]
    assert monitor["stats"]["total_runs"] == 3
    assert monitor["stats"]["success_count"] == 1
    assert monitor["stats"]["failure_count"] == 2
    assert monitor["stats"]["uptime_pct"] == pytest.approx(33.33)
    assert monitor["failing"] is True

    assert [e["message"] for e in context["errors"]] == ["connection refused"]
    assert context["alerts"][0]["name"] == "disk"
    assert context["alerts"][0]["threshold"] == 90
    assert context["summary"]["failing"] == ["backup-db"]
    assert context["summary"]["uptime_pct"] == pytest.approx(33.33)


@pytest.mark.unit
def test_errors_and_alerts_are_empty_when_nothing_failed(repository):
    _save_run(repository, "backup-db", days_ago=1)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config([{"match": "*", "fields": {"status": {"mode": "latest"}}}]),
        now=NOW,
    )

    assert context["errors"] == []
    assert context["alerts"] == []
    assert context["summary"]["uptime_pct"] == 100.0


@pytest.mark.unit
def test_a_monitor_with_no_history_still_renders(repository, monkeypatch):
    builder = ReportDataBuilder(repository=repository)
    monkeypatch.setattr(builder, "_candidate_names", lambda: ["never-run"])

    context = builder.build(
        _config(
            [{"match": "*", "fields": {"run_time": {"mode": "range", "last": "7d"}}}]
        ),
        now=NOW,
    )

    monitor = context["monitors"][0]
    assert monitor["stats"]["total_runs"] == 0
    assert monitor["fields"]["run_time"]["values"] == []
    assert monitor["fields"]["run_time"]["avg"] is None
    assert context["summary"]["uptime_pct"] is None


@pytest.mark.unit
def test_an_open_ended_field_keeps_the_window_open_whatever_the_order(repository):
    """An `until` bound must not truncate a field that has none."""
    bounded = {"mode": "range", "since": "2025-01-01", "until": "2025-02-01"}
    open_ended = {"mode": "range", "last": "30d"}

    builder = ReportDataBuilder(repository=repository)
    forwards = builder._report_window(
        _config([{"match": "*", "fields": {"a": open_ended, "b": bounded}}]),
        {"m": {"a": _spec("a", open_ended), "b": _spec("b", bounded)}},
        NOW,
    )
    backwards = builder._report_window(
        _config([{"match": "*", "fields": {"b": bounded, "a": open_ended}}]),
        {"m": {"b": _spec("b", bounded), "a": _spec("a", open_ended)}},
        NOW,
    )

    assert forwards == backwards
    assert forwards[1] is None


@pytest.mark.unit
def test_an_open_ended_field_still_sees_recent_runs(repository):
    _save_run(repository, "backup-db", days_ago=1, duration=2.0)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [
                {
                    "match": "backup-db",
                    "fields": {
                        "run_time": {"mode": "range", "last": "30d"},
                        "old": {
                            "mode": "range",
                            "since": "2025-01-01",
                            "until": "2025-02-01",
                        },
                    },
                }
            ]
        ),
        now=NOW,
    )

    assert context["monitors"][0]["fields"]["run_time"]["values"] == [2.0]


@pytest.mark.unit
def test_a_latest_field_does_not_widen_the_section_window(repository):
    """`latest` is resolved on its own - it must not discard default_window."""
    _save_run(
        repository,
        "backup-db",
        days_ago=90,
        status=ResultStatus.ERROR,
        error="ancient failure",
    )
    _save_run(repository, "backup-db", days_ago=1)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [{"match": "backup-db", "fields": {"status": {"mode": "latest"}}}],
            default_window="30d",
        ),
        now=NOW,
    )

    assert context["window"]["since"] == NOW - timedelta(days=30)
    # the 90 day old failure is outside the window the page reports on
    assert context["errors"] == []
    assert context["monitors"][0]["stats"]["total_runs"] == 1


@pytest.mark.unit
def test_latest_still_finds_a_value_recorded_before_the_window(repository):
    _save_run(repository, "backup-db", days_ago=90, status=ResultStatus.ALERT)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [{"match": "backup-db", "fields": {"status": {"mode": "latest"}}}],
            default_window="30d",
        ),
        now=NOW,
    )

    status = context["monitors"][0]["fields"]["status"]
    assert status["value"] == "alert"
    assert status["at"] == NOW - timedelta(days=90)
    # ...without dragging the window's stats back with it
    assert context["monitors"][0]["stats"]["total_runs"] == 0


@pytest.mark.unit
def test_max_runs_caps_each_monitor_separately(repository):
    for days_ago in range(1, 6):
        _save_run(repository, "chatty", days_ago=days_ago)
    _save_run(repository, "quiet", days_ago=1)

    builder = ReportDataBuilder(repository=repository, max_runs=3)
    context = builder.build(
        _config([{"match": "*", "fields": {"status": {"mode": "latest"}}}]),
        now=NOW,
    )

    counts = {m["name"]: m["stats"]["total_runs"] for m in context["monitors"]}
    # the busy monitor is capped, the quiet one is not starved by it
    assert counts == {"chatty": 3, "quiet": 1}


@pytest.mark.unit
def test_state_is_read_once_for_the_whole_report(repository):
    _save_run(repository, "backup-db", days_ago=1)
    _save_run(repository, "web-front", days_ago=1)

    builder = ReportDataBuilder(repository=repository)
    calls = []
    original = repository.get_all_state
    repository.get_all_state = lambda: (calls.append(1), original())[1]
    repository.get_state = lambda name: pytest.fail("get_state called per monitor")

    context = builder.build(
        _config([{"match": "*", "fields": {"status": {"mode": "latest"}}}]),
        now=NOW,
    )

    assert len(calls) == 1
    assert [m["last_status"] for m in context["monitors"]] == ["ok", "ok"]


@pytest.mark.unit
def test_exclude_drops_a_matched_monitor(repository):
    _save_run(repository, "backup-db", days_ago=1)
    _save_run(repository, "backup-old", days_ago=1)

    builder = ReportDataBuilder(repository=repository)
    context = builder.build(
        _config(
            [
                {
                    "match": "backup-*",
                    "exclude": "backup-old",
                    "fields": {"status": {"mode": "latest"}},
                }
            ]
        ),
        now=NOW,
    )

    assert [m["name"] for m in context["monitors"]] == ["backup-db"]


@pytest.mark.unit
def test_tags_come_from_monitor_definitions(repository, tmp_path):
    from locallib.monitor_loader import MonitorLoader

    _save_run(repository, "nightly-job", days_ago=1)
    _save_run(repository, "daytime-job", days_ago=1)

    (tmp_path / "nightly.toml").write_text(
        '[settings]\nname = "nightly-job"\ntags = ["nightly", "batch"]\n'
    )
    (tmp_path / "daytime.toml").write_text('[settings]\nname = "daytime-job"\n')

    builder = ReportDataBuilder(
        repository=repository, monitor_loader=MonitorLoader(str(tmp_path))
    )
    context = builder.build(
        _config([{"tags": "nightly", "fields": {"status": {"mode": "latest"}}}]),
        now=NOW,
    )

    assert [m["name"] for m in context["monitors"]] == ["nightly-job"]
    assert context["monitors"][0]["tags"] == ["nightly", "batch"]
