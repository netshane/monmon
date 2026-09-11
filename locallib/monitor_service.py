"""Orchestrates loading, scheduling, running, storing, and notifying."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from .hierarchy_resolver import HierarchyResolver, ParentFailure
from .monitor_loader import MonitorLoader
from .monitor_models import MonitorDefinition
from .monitor_repository import MonitorRepository
from .monitor_results import Alert, MonitorResult, ResultStatus
from .monitor_runner import MonitorRunner
from .notifier import Notifier
from .schedule_calculator import ScheduleCalculator


@dataclass
class ScheduleEntry:
    """Scheduling state for one monitor."""

    name: str
    active: bool
    scheduled: bool
    last_run_at: datetime | None
    last_status: str | None
    next_run_at: datetime | None
    due: bool


class MonitorService:
    """The application service the CLI drives."""

    def __init__(
        self,
        loader: MonitorLoader,
        runner: MonitorRunner,
        repository: MonitorRepository,
        schedule_calculator: ScheduleCalculator,
        notifier: Notifier,
        persist_results: bool = True,
    ):
        self.loader = loader
        self.runner = runner
        self.repository = repository
        self.schedule_calculator = schedule_calculator
        self.notifier = notifier
        self.persist_results = persist_results
        self._monitors: dict[str, MonitorDefinition] | None = None
        self._hierarchy: HierarchyResolver | None = None

    # -- loading ---------------------------------------------------------

    def monitors(self, reload: bool = False) -> dict[str, MonitorDefinition]:
        if self._monitors is None or reload:
            self._monitors = self.loader.load_all()
            self._hierarchy = HierarchyResolver(self._monitors)
            logger.info(
                f"Loaded {len(self._monitors)} monitor(s) from {self.loader.monitors_path}"
            )
        return self._monitors

    def hierarchy(self) -> HierarchyResolver:
        self.monitors()
        assert self._hierarchy is not None
        return self._hierarchy

    def get_monitor(self, name: str) -> MonitorDefinition:
        monitors = self.monitors()
        if name in monitors:
            return monitors[name]

        lowered = name.lower()
        for monitor_name, definition in monitors.items():
            if monitor_name.lower() == lowered:
                return definition

        raise KeyError(f"No monitor named '{name}' was found")

    def validate(self) -> list[str]:
        """Validate the loaded monitor set, returning warnings."""
        self.monitors()

        return list(self.loader.load_errors) + self.hierarchy().validate()

    # -- scheduling ------------------------------------------------------

    def schedule_entries(self, now: datetime | None = None) -> list[ScheduleEntry]:
        """Next run time and due state for every monitor."""
        now = now or self.schedule_calculator.now()
        state = self.repository.get_all_state()
        definitions = self.monitors()

        # counting failure streaks scans run history, so only pay for it when
        # a recheck could actually apply somewhere
        failure_counts = (
            self.repository.consecutive_failure_counts()
            if self.schedule_calculator.rechecks_possible(
                definition.schedule for definition in definitions.values()
            )
            else {}
        )
        entries: list[ScheduleEntry] = []

        for name, definition in sorted(definitions.items()):
            monitor_state = state.get(name) or {}
            last_run = monitor_state.get("last_run_at")
            last_status = monitor_state.get("last_status")
            next_run = None
            due = False

            if definition.active:
                next_run = self.schedule_calculator.next_run(
                    definition.schedule,
                    last_run,
                    now,
                    last_status=last_status,
                    # the run that first failed is not itself a retry, so the
                    # attempt count is the failure streak less that first run
                    attempt_count=max(failure_counts.get(name, 0) - 1, 0),
                )
                due = next_run is not None and next_run <= now

            entries.append(
                ScheduleEntry(
                    name=name,
                    active=definition.active,
                    scheduled=definition.schedule.is_scheduled,
                    last_run_at=last_run,
                    last_status=last_status,
                    next_run_at=next_run,
                    due=due,
                )
            )

        return entries

    def store_next_runs(self, entries: list[ScheduleEntry] | None = None):
        """Persist the calculated next run time of every monitor."""
        for entry in entries or self.schedule_entries():
            self.repository.set_next_run(entry.name, entry.next_run_at)

    def due_monitors(self, now: datetime | None = None) -> list[str]:
        return [entry.name for entry in self.schedule_entries(now) if entry.due]

    # -- running ---------------------------------------------------------

    def run_monitor(self, name: str, force: bool = False) -> MonitorResult:
        """Run one monitor by name."""
        definition = self.get_monitor(name)

        result = self._run_definition(definition, force=force)
        self.notifier.flush()

        return result

    def run_scheduled(self, now: datetime | None = None) -> list[MonitorResult]:
        """Work out what is due and run all of it.

        Monitors are run parents first so a child sees its parent's fresh
        status when `skip_on_parent_fail` applies.
        """
        now = now or self.schedule_calculator.now()

        self.validate()
        entries = self.schedule_entries(now)
        if self.persist_results:
            self.store_next_runs(entries)

        due = [entry.name for entry in entries if entry.due]
        if not due:
            logger.info("No monitors are currently scheduled to run")
            return []

        logger.info(f"{len(due)} monitor(s) due: {', '.join(due)}")

        results = []
        for name in self._order_by_hierarchy(due):
            results.append(self._run_definition(self.get_monitor(name)))

        self.notifier.flush()
        if self.persist_results:
            self.store_next_runs()

        return results

    def run_monitors(
        self, names: list[str], force: bool = False
    ) -> list[MonitorResult]:
        results = []
        for name in names:
            try:
                definition = self.get_monitor(name)
            except KeyError as e:
                logger.error(str(e))
                results.append(
                    MonitorResult(
                        monitor_name=name, status=ResultStatus.ERROR, message=str(e)
                    )
                )
                continue

            results.append(self._run_definition(definition, force=force))

        self.notifier.flush()

        return results

    def _run_definition(
        self, definition: MonitorDefinition, force: bool = False
    ) -> MonitorResult:
        if not definition.active and not force:
            logger.info(f"Monitor '{definition.name}' is not active - skipping")
            return MonitorResult(
                monitor_name=definition.name,
                status=ResultStatus.INACTIVE,
                finished_at=datetime.now(),
                message="Monitor is not active",
            )

        failures = self._parent_failures(definition.name)
        skipping = [f for f in failures if f.skip]

        if skipping and not force:
            message = "; ".join(
                f"hierarchy '{f.hierarchy}' parent '{f.parent_node}' failed "
                f"({', '.join(f.monitor_names)})"
                for f in skipping
            )
            logger.warning(f"Skipping monitor '{definition.name}': {message}")
            result = MonitorResult(
                monitor_name=definition.name,
                status=ResultStatus.SKIPPED,
                finished_at=datetime.now(),
                message=f"Skipped - {message}",
            )
            self._persist(result)
            return result

        result = self.runner.run(
            definition, forced_alerts=self._hierarchy_alerts(failures)
        )
        self._persist(result)

        try:
            self.notifier.notify(definition, result)
        except Exception as e:
            logger.exception(f"Notification failed for '{definition.name}': {e}")

        return result

    def _persist(self, result: MonitorResult):
        if not self.persist_results:
            logger.debug(
                f"Not storing result for '{result.monitor_name}' (persistence disabled)"
            )
            return

        try:
            self.repository.save_result(result)
        except Exception as e:
            logger.exception(f"Failed to store result for '{result.monitor_name}': {e}")

    def _parent_failures(self, monitor_name: str) -> list[ParentFailure]:
        statuses: dict[str, ResultStatus | None] = {}
        for name, state in self.repository.get_all_state().items():
            raw_status = state.get("last_status")
            try:
                statuses[name] = ResultStatus(raw_status) if raw_status else None
            except ValueError:
                statuses[name] = None

        return self.hierarchy().parent_failures(monitor_name, statuses)

    @staticmethod
    def _hierarchy_alerts(failures: list[ParentFailure]) -> list[Alert]:
        return [
            Alert(
                name=failure.parent_node,
                message=f"Parent node '{failure.parent_node}' in hierarchy "
                f"'{failure.hierarchy}' has failed "
                f"({', '.join(failure.monitor_names)})",
                details="alert_on_parent_fail is set for this monitor",
            )
            for failure in failures
            if failure.alert
        ]

    def _order_by_hierarchy(self, names: list[str]) -> list[str]:
        """Sort monitor names so parents come before their children."""
        depths = {name: self._depth(name) for name in names}

        return sorted(names, key=lambda name: (depths[name], name))

    def _depth(self, name: str, seen: set[str] | None = None) -> int:
        """Deepest distance to a root node across all of a monitor's hierarchies."""
        seen = seen or set()
        if name in seen:
            return 0
        seen = seen | {name}

        definition = self.monitors().get(name)
        if definition is None:
            return 0

        hierarchy = self.hierarchy()
        depth = 0
        for hierarchy_name, config in definition.hierarchies.items():
            if not config.parent_node:
                continue
            for parent_name in hierarchy.monitors_for_node(
                hierarchy_name, config.parent_node
            ):
                depth = max(depth, 1 + self._depth(parent_name, seen))

        return depth
