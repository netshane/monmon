"""Result objects produced by running monitor tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ResultStatus(str, Enum):
    OK = "ok"
    ALERT = "alert"
    ERROR = "error"
    SKIPPED = "skipped"
    INACTIVE = "inactive"

    @property
    def is_failure(self) -> bool:
        return self in (ResultStatus.ALERT, ResultStatus.ERROR)


@dataclass
class Alert:
    """A single alert raised by a test."""

    message: str
    name: str | None = None
    value: object | None = None
    threshold: object | None = None
    details: str | None = None

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "name": self.name,
            "value": self.value,
            "threshold": self.threshold,
            "details": self.details,
        }


@dataclass
class Report:
    """A tabular report produced by a report test."""

    title: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"title": self.title, "columns": self.columns, "rows": self.rows}

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass
class TestResult:
    """The outcome of one `[test.*]` section."""

    # keeps pytest from trying to collect this class as a test case
    __test__ = False

    key: str
    test_type: str
    status: ResultStatus = ResultStatus.OK
    message: str | None = None
    value: object | None = None
    alerts: list[Alert] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "test_type": self.test_type,
            "status": self.status.value,
            "message": self.message,
            "value": self.value,
            "alerts": [a.to_dict() for a in self.alerts],
            "reports": [r.to_dict() for r in self.reports],
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class MonitorResult:
    """The outcome of running every test in a monitor."""

    monitor_name: str
    status: ResultStatus = ResultStatus.OK
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    message: str | None = None
    test_results: list[TestResult] = field(default_factory=list)
    run_id: int | None = None

    @property
    def alerts(self) -> list[Alert]:
        return [alert for result in self.test_results for alert in result.alerts]

    @property
    def reports(self) -> list[Report]:
        return [report for result in self.test_results for report in result.reports]

    @property
    def errors(self) -> list[str]:
        return [r.error for r in self.test_results if r.error]

    @property
    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def rollup_status(self) -> ResultStatus:
        """Worst status across all tests wins."""
        statuses = [r.status for r in self.test_results]
        for status in (ResultStatus.ERROR, ResultStatus.ALERT):
            if status in statuses:
                return status
        if statuses and all(s == ResultStatus.SKIPPED for s in statuses):
            return ResultStatus.SKIPPED

        return ResultStatus.OK

    def to_dict(self) -> dict:
        return {
            "monitor_name": self.monitor_name,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at if self.finished_at else None,
            "message": self.message,
            "test_results": [r.to_dict() for r in self.test_results],
        }
