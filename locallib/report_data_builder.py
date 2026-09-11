"""Turns a report definition plus stored history into template context.

Everything here reads through `MonitorRepository`, so the same code works
against sqlite today and postgres later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase

from loguru import logger

from .helpers import to_number
from .monitor_loader import MonitorLoader
from .monitor_models import MonitorDefinition
from .monitor_repository import MonitorRepository
from .monitor_results import ResultStatus
from .report_models import SECTION_NAMES, FieldSpec, ReportConfig

# run level fields addressable from report.yaml, mapped to their run column
RUN_FIELDS = {
    "status": "status",
    "message": "message",
    "run_time": "duration_seconds",
    "runtime": "duration_seconds",
    "duration": "duration_seconds",
    "duration_seconds": "duration_seconds",
    "started_at": "started_at",
    "finished_at": "finished_at",
    "alert_count": "alert_count",
    "report_count": "report_count",
    "error_count": "error_count",
}

FAILURE_STATUSES = {ResultStatus.ALERT.value, ResultStatus.ERROR.value}


@dataclass
class LoadedHistory:
    """Run history for one report, grouped for lookup.

    `runs` holds the report window (what the stats and the error / alert
    sections describe).  `latest_runs` is that set widened with each monitor's
    most recent runs, so a `latest` field can still find a value recorded
    before the window opened.
    """

    runs: dict[str, list[dict]]
    latest_runs: dict[str, list[dict]]
    tests: dict[int, list[dict]]


class ReportDataBuilder:
    """Resolves a `ReportConfig` into the context a template renders."""

    def __init__(
        self,
        repository: MonitorRepository,
        monitor_loader: MonitorLoader | None = None,
        max_runs: int | None = 5000,
        max_latest_runs: int = 50,
    ):
        self.repository = repository
        self.monitor_loader = monitor_loader
        # per monitor caps: history loaded for the report window, and how far
        # back a `latest` field looks when the window holds no value for it
        self.max_runs = max_runs
        self.max_latest_runs = max_latest_runs
        self._definitions: dict[str, MonitorDefinition] | None = None

    # -- public ----------------------------------------------------------

    def build(self, config: ReportConfig, now: datetime | None = None) -> dict:
        """Build the full template context for one report."""
        now = now or datetime.now()

        selected = self._select_monitors(config)
        since, until = self._report_window(config, selected, now)
        history = self._load_history(selected, since, until)
        state = self.repository.get_all_state()

        monitors = [
            self._build_monitor(
                name=name,
                fields=fields,
                runs=history.runs.get(name, []),
                latest_runs=history.latest_runs.get(name, []),
                tests_by_run=history.tests,
                state=state.get(name) or {},
                now=now,
            )
            for name, fields in selected.items()
        ]

        errors = _newest_first([e for m in monitors for e in m["errors"]])
        alerts = _newest_first([a for m in monitors for a in m["alerts"]])

        return {
            "title": config.title,
            "subtitle": config.subtitle,
            "branding": config.branding,
            "report_name": config.name,
            "generated_at": now,
            "sections": {
                name: config.section_enabled(name)
                for name in set(SECTION_NAMES) | set(config.sections)
            },
            "window": {"since": since, "until": until},
            "monitors": monitors,
            "errors": errors,
            "alerts": alerts,
            "summary": self._summarize(monitors),
            "config": config,
            "extra": config.extra,
        }

    # -- monitor selection ------------------------------------------------

    def definitions(self) -> dict[str, MonitorDefinition]:
        """Monitor definitions, loaded once, empty when no loader was given."""
        if self._definitions is None:
            if self.monitor_loader is None:
                self._definitions = {}
            else:
                try:
                    self._definitions = self.monitor_loader.load_all()
                except Exception as e:
                    logger.warning(f"Could not load monitor definitions: {e}")
                    self._definitions = {}

        return self._definitions

    def _candidate_names(self) -> list[str]:
        names = set(self.definitions())
        names.update(self.repository.get_monitor_names())

        return sorted(names)

    def _select_monitors(self, config: ReportConfig) -> dict[str, dict[str, FieldSpec]]:
        """Monitor name -> field specs, for every monitor the report includes.

        Entries are applied in order, so a later entry's field of the same name
        wins for monitors matched by both.
        """
        definitions = self.definitions()
        selected: dict[str, dict[str, FieldSpec]] = {}

        for name in self._candidate_names():
            definition = definitions.get(name)
            tags = definition.tags if definition else []

            for selector in config.monitors:
                if not selector.matches(name, tags):
                    continue
                selected.setdefault(name, {}).update(selector.fields)

        return selected

    # -- history loading --------------------------------------------------

    def _report_window(
        self,
        config: ReportConfig,
        selected: dict[str, dict[str, FieldSpec]],
        now: datetime,
    ) -> tuple[datetime | None, datetime | None]:
        """The window covering every `range` field, and the report's sections.

        `latest` fields are deliberately ignored here - they are resolved from
        their own lookup, so one `latest` field no longer drags the whole
        report (and its summary / errors / alerts sections) back to the start
        of history.  Returns `(None, None)` when a range field is unbounded and
        no `default_window` caps it.
        """
        specs = [
            spec
            for fields in selected.values()
            for spec in fields.values()
            if spec.is_range
        ]
        default_since = now - config.default_window if config.default_window else None

        if not specs:
            return default_since, None

        since: datetime | None = None
        until: datetime | None = None
        unbounded_since = False
        unbounded_until = False

        for spec in specs:
            spec_since, spec_until = spec.window(now)

            if spec_since is None:
                unbounded_since = True
            elif since is None or spec_since < since:
                since = spec_since

            # an open ended field keeps the window open, whatever order the
            # fields were declared in
            if spec_until is None:
                unbounded_until = True
            elif until is None or spec_until > until:
                until = spec_until

        if unbounded_since:
            since = default_since
        elif default_since is not None and since is not None:
            since = min(since, default_since)

        return since, (None if unbounded_until else until)

    def _load_history(
        self,
        selected: dict[str, dict[str, FieldSpec]],
        since: datetime | None,
        until: datetime | None,
    ) -> "LoadedHistory":
        """Fetch runs and their test results, grouped for fast lookup.

        History is fetched per monitor so `max_runs` caps each monitor rather
        than being consumed by the busiest one.  Monitors with a `latest` field
        get a second, unbounded-in-time lookup of their most recent runs, so
        `latest` still finds a value recorded before the report window.
        """
        runs_by_monitor: dict[str, list[dict]] = {}
        latest_by_monitor: dict[str, list[dict]] = {}
        windowed = since is not None or until is not None

        for name, fields in selected.items():
            runs = self.repository.get_runs(
                monitor_name=name, since=since, until=until, limit=self.max_runs
            )
            if self.max_runs is not None and len(runs) == self.max_runs:
                logger.warning(
                    f"Report history for '{name}' was truncated at {self.max_runs} "
                    f"run(s) - raise max_runs or narrow the report window"
                )

            latest = runs
            if windowed and any(not spec.is_range for spec in fields.values()):
                latest = _merge_runs(
                    runs,
                    self.repository.get_runs(
                        monitor_name=name, limit=self.max_latest_runs
                    ),
                )

            # the repository returns newest first - reports read oldest first
            runs_by_monitor[name] = list(reversed(runs))
            latest_by_monitor[name] = list(reversed(latest))

        run_ids = {run["id"] for runs in latest_by_monitor.values() for run in runs}

        tests_by_run: dict[int, list[dict]] = {}
        for chunk in _chunks(sorted(run_ids), 500):
            for test in self.repository.get_test_results(run_ids=chunk):
                tests_by_run.setdefault(test["run_id"], []).append(test)

        return LoadedHistory(
            runs=runs_by_monitor, latest_runs=latest_by_monitor, tests=tests_by_run
        )

    # -- per monitor ------------------------------------------------------

    def _build_monitor(
        self,
        name: str,
        fields: dict[str, FieldSpec],
        runs: list[dict],
        latest_runs: list[dict],
        tests_by_run: dict[int, list[dict]],
        state: dict,
        now: datetime,
    ) -> dict:
        definition = self.definitions().get(name)

        # stats and the error / alert sections describe the report window;
        # `latest` fields read from the wider lookup
        resolved = {
            field_name: self._resolve_field(
                spec,
                latest_runs if not spec.is_range else runs,
                tests_by_run,
                now,
            )
            for field_name, spec in fields.items()
        }

        return {
            "name": name,
            "description": definition.description if definition else None,
            "link": definition.link if definition else None,
            "tags": definition.tags if definition else [],
            "active": definition.active if definition else None,
            "fields": resolved,
            "stats": _run_stats(runs),
            "runs": runs,
            "errors": _errors_from(name, runs, tests_by_run),
            "alerts": _alerts_from(name, runs, tests_by_run),
            "last_run_at": state.get("last_run_at"),
            "last_status": state.get("last_status"),
            "next_run_at": state.get("next_run_at"),
            "failing": state.get("last_status") in FAILURE_STATUSES,
        }

    def _resolve_field(
        self,
        spec: FieldSpec,
        runs: list[dict],
        tests_by_run: dict[int, list[dict]],
        now: datetime,
    ) -> dict:
        points = _points_for(spec.name, runs, tests_by_run)
        since, until = spec.window(now)

        if since is not None:
            points = [p for p in points if p["at"] is None or p["at"] >= since]
        if until is not None:
            points = [p for p in points if p["at"] is None or p["at"] <= until]

        resolved = {
            "name": spec.name,
            "label": spec.label or spec.name.replace("_", " "),
            "mode": spec.mode,
            "since": since,
            "until": until,
        }

        if not spec.is_range:
            latest = points[-1] if points else None
            resolved.update(
                {
                    "value": latest["value"] if latest else None,
                    "at": latest["at"] if latest else None,
                    "point": latest,
                    "points": [latest] if latest else [],
                    "values": [latest["value"]] if latest else [],
                    "count": 1 if latest else 0,
                }
            )
            return resolved

        values = [p["value"] for p in points]
        numbers = [n for n in (to_number(v) for v in values) if n is not None]

        resolved.update(
            {
                "points": points,
                "values": values,
                "count": len(points),
                "first": points[0] if points else None,
                "last": points[-1] if points else None,
                "value": values[-1] if values else None,
                "at": points[-1]["at"] if points else None,
                "numbers": numbers,
                "min": min(numbers) if numbers else None,
                "max": max(numbers) if numbers else None,
                "avg": (sum(numbers) / len(numbers)) if numbers else None,
            }
        )

        return resolved

    # -- summary ----------------------------------------------------------

    @staticmethod
    def _summarize(monitors: list[dict]) -> dict:
        total = sum(m["stats"]["total_runs"] for m in monitors)
        success = sum(m["stats"]["success_count"] for m in monitors)
        failure = sum(m["stats"]["failure_count"] for m in monitors)
        durations = [
            m["stats"]["avg_duration"]
            for m in monitors
            if m["stats"]["avg_duration"] is not None
        ]

        return {
            "monitor_count": len(monitors),
            "total_runs": total,
            "success_count": success,
            "failure_count": failure,
            "uptime_pct": _percent(success, success + failure),
            "error_count": sum(len(m["errors"]) for m in monitors),
            "alert_count": sum(len(m["alerts"]) for m in monitors),
            "failing": [m["name"] for m in monitors if m["failing"]],
            "avg_duration": (sum(durations) / len(durations)) if durations else None,
        }


# -- field extraction -----------------------------------------------------


def _points_for(
    field_name: str, runs: list[dict], tests_by_run: dict[int, list[dict]]
) -> list[dict]:
    """Every recorded value of `field_name`, oldest first.

    Run level names (`status`, `run_time`, ...) come from the run row, the
    special names `errors` / `alerts` from its test results, and anything else
    is matched against test keys and test types (glob patterns allowed).
    """
    if field_name in RUN_FIELDS:
        column = RUN_FIELDS[field_name]
        return [_point(run, run.get(column)) for run in runs]

    if field_name == "errors":
        return [
            _point(run, test["error"], test)
            for run in runs
            for test in tests_by_run.get(run["id"], [])
            if test.get("error")
        ]

    if field_name == "alerts":
        return [
            _point(run, alert.get("message"), test, extra={"alert": alert})
            for run in runs
            for test in tests_by_run.get(run["id"], [])
            for alert in _test_alerts(test)
        ]

    points = []
    for run in runs:
        for test in tests_by_run.get(run["id"], []):
            if _matches_test(field_name, test):
                points.append(_point(run, test.get("value"), test))

    return points


def _matches_test(field_name: str, test: dict) -> bool:
    key = test.get("test_key") or ""
    test_type = test.get("test_type") or ""

    return (
        key == field_name
        or test_type == field_name
        or fnmatchcase(key, field_name)
        or fnmatchcase(test_type, field_name)
    )


def _point(
    run: dict, value, test: dict | None = None, extra: dict | None = None
) -> dict:
    point = {
        "at": run.get("started_at"),
        "value": value,
        "number": to_number(value),
        "status": run.get("status"),
        "run_id": run.get("id"),
        "monitor": run.get("monitor_name"),
        "test_key": test.get("test_key") if test else None,
        "test_type": test.get("test_type") if test else None,
    }
    if extra:
        point.update(extra)

    return point


def _test_alerts(test: dict) -> list[dict]:
    """Alerts stored inside a test result's json blob."""
    raw = test.get("result_json")
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.debug(f"Unreadable result_json for test '{test.get('test_key')}'")
        return []

    alerts = data.get("alerts") or []

    return [a for a in alerts if isinstance(a, dict)]


# -- aggregation ----------------------------------------------------------


def _run_stats(runs: list[dict]) -> dict:
    durations = [d for d in (r.get("duration_seconds") for r in runs) if d is not None]
    success = sum(1 for r in runs if r.get("status") == ResultStatus.OK.value)
    failure = sum(1 for r in runs if r.get("status") in FAILURE_STATUSES)

    return {
        "total_runs": len(runs),
        "success_count": success,
        "failure_count": failure,
        "skipped_count": sum(
            1 for r in runs if r.get("status") == ResultStatus.SKIPPED.value
        ),
        "uptime_pct": _percent(success, success + failure),
        "min_duration": min(durations) if durations else None,
        "max_duration": max(durations) if durations else None,
        "avg_duration": (sum(durations) / len(durations)) if durations else None,
        "durations": durations,
        "first_run_at": runs[0].get("started_at") if runs else None,
        "last_run_at": runs[-1].get("started_at") if runs else None,
    }


def _errors_from(
    monitor_name: str, runs: list[dict], tests_by_run: dict[int, list[dict]]
) -> list[dict]:
    errors = []

    for run in runs:
        tests = tests_by_run.get(run["id"], [])
        found = False

        for test in tests:
            if not test.get("error"):
                continue
            found = True
            errors.append(
                {
                    "monitor": monitor_name,
                    "at": run.get("started_at"),
                    "run_id": run.get("id"),
                    "test_key": test.get("test_key"),
                    "test_type": test.get("test_type"),
                    "message": test.get("error"),
                    "detail": test.get("message"),
                }
            )

        if not found and run.get("status") == ResultStatus.ERROR.value:
            errors.append(
                {
                    "monitor": monitor_name,
                    "at": run.get("started_at"),
                    "run_id": run.get("id"),
                    "test_key": None,
                    "test_type": None,
                    "message": run.get("message") or "Monitor run failed",
                    "detail": None,
                }
            )

    return errors


def _alerts_from(
    monitor_name: str, runs: list[dict], tests_by_run: dict[int, list[dict]]
) -> list[dict]:
    alerts = []

    for run in runs:
        for test in tests_by_run.get(run["id"], []):
            for alert in _test_alerts(test):
                alerts.append(
                    {
                        "monitor": monitor_name,
                        "at": run.get("started_at"),
                        "run_id": run.get("id"),
                        "test_key": test.get("test_key"),
                        "name": alert.get("name"),
                        "message": alert.get("message"),
                        "details": alert.get("details"),
                        "value": alert.get("value"),
                        "threshold": alert.get("threshold"),
                    }
                )

    return alerts


def _percent(part: int, total: int) -> float | None:
    if not total:
        return None

    return round(100.0 * part / total, 2)


def _newest_first(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: e.get("at") or datetime.min, reverse=True)


def _merge_runs(*run_lists: list[dict]) -> list[dict]:
    """Combine run lists, dropping duplicates, keeping newest first."""
    merged: dict[int, dict] = {}
    for runs in run_lists:
        for run in runs:
            merged.setdefault(run["id"], run)

    return sorted(
        merged.values(),
        key=lambda run: run.get("started_at") or datetime.min,
        reverse=True,
    )


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]
