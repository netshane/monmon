"""Runs the tests of a single monitor."""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from .db_connection_factory import DbConnectionFactory
from .docker_service import DockerServiceFactory
from .monitor_models import MonitorDefinition
from .monitor_results import Alert, MonitorResult, ResultStatus, TestResult
from .monitor_test_factory import MonitorTestFactory
from .opensearch_service import OpenSearchServiceFactory


class MonitorRunner:
    """Executes every test in a monitor and rolls the results up.

    Connections declared in a monitor's `[connections]` section are added to
    the shared connection factories before its tests run, so a monitor can
    supply connections that are not in `settings.toml`. The overrides are
    scoped to this run and restored once it finishes, so they never leak
    into another monitor's run.
    """

    def __init__(
        self,
        test_factory: MonitorTestFactory,
        db_factory: DbConnectionFactory,
        docker_factory: DockerServiceFactory,
        opensearch_factory: OpenSearchServiceFactory,
        simulate: bool = False,
    ):
        self.test_factory = test_factory
        self.db_factory = db_factory
        self.docker_factory = docker_factory
        self.opensearch_factory = opensearch_factory
        self.simulate = simulate

    def run(
        self,
        definition: MonitorDefinition,
        forced_alerts: list[Alert] | None = None,
    ) -> MonitorResult:
        result = MonitorResult(monitor_name=definition.name, started_at=datetime.now())

        db_connections = self.db_factory.connections_snapshot()
        docker_connections = self.docker_factory.connections_snapshot()
        search_connections = self.opensearch_factory.connections_snapshot()
        try:
            self._register_connections(definition)

            if not definition.tests:
                logger.warning(f"Monitor '{definition.name}' has no [test.*] sections")
                result.message = "Monitor has no tests"

            for test_config in definition.tests:
                result.test_results.append(self._run_test(definition, test_config))
        finally:
            self.db_factory.restore_connections(db_connections)
            self.docker_factory.restore_connections(docker_connections)
            self.opensearch_factory.restore_connections(search_connections)

        for alert in forced_alerts or []:
            result.test_results.append(
                TestResult(
                    key=f"{definition.name}.hierarchy",
                    test_type="hierarchy",
                    status=ResultStatus.ALERT,
                    message=alert.message,
                    alerts=[alert],
                )
            )

        result.status = result.rollup_status()
        result.finished_at = datetime.now()

        if result.message is None:
            result.message = self._summarise(result)

        logger.info(
            f"Monitor '{definition.name}' finished with status {result.status.value} "
            f"({len(result.alerts)} alert(s), {len(result.reports)} report(s)) "
            f"in {result.duration_seconds:.2f}s"
        )

        return result

    def _run_test(self, definition: MonitorDefinition, test_config) -> TestResult:
        try:
            test = self.test_factory.create(test_config)
        except Exception as e:
            logger.error(
                f"Monitor '{definition.name}' test '{test_config.key}' could not be "
                f"created: {e}"
            )
            return TestResult(
                key=test_config.key,
                test_type=test_config.test_type,
                status=ResultStatus.ERROR,
                message=str(e),
                error=str(e),
            )

        if self.simulate:
            logger.info(
                f"SIMULATE: would run '{test_config.full_name}' test "
                f"'{test_config.key}' for monitor '{definition.name}'"
            )
            return TestResult(
                key=test_config.key,
                test_type=test_config.test_type,
                status=ResultStatus.SKIPPED,
                message="Skipped - simulate mode",
            )

        logger.debug(
            f"Running '{test_config.full_name}' test '{test_config.key}' for "
            f"monitor '{definition.name}'"
        )

        return test.run()

    def _register_connections(self, definition: MonitorDefinition):
        if not definition.connections:
            return

        db_connections = {}
        docker_connections = {}
        search_connections = {}

        for name, value in definition.connections.items():
            if isinstance(value, dict):
                # a table naming a docker_host is a docker connection, any
                # other table is an opensearch one
                if value.get("docker_host"):
                    docker_connections[name] = value
                else:
                    search_connections[name] = value
            elif str(value).strip():
                db_connections[name] = value
            else:
                logger.debug(
                    f"Monitor '{definition.name}' connection '{name}' is empty - "
                    f"falling back to the connection from settings"
                )

        if db_connections:
            self.db_factory.add_connections(db_connections)
        if docker_connections:
            self.docker_factory.add_connections(docker_connections)
        if search_connections:
            self.opensearch_factory.add_connections(search_connections)

    @staticmethod
    def _summarise(result: MonitorResult) -> str:
        parts = [f"{len(result.test_results)} test(s)"]
        if result.alerts:
            parts.append(f"{len(result.alerts)} alert(s)")
        if result.reports:
            parts.append(f"{len(result.reports)} report(s)")
        if result.errors:
            parts.append(f"{len(result.errors)} error(s)")

        return ", ".join(parts)
