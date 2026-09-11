"""Docker backed monitor tests."""

from __future__ import annotations

from ..docker_service import (
    RESTARTING_STATE,
    ContainerStatus,
    DockerServiceFactory,
    DockerUnavailable,
)
from ..monitor_models import TestConfig
from ..monitor_results import Alert, ResultStatus, TestResult
from ..value_expander import ValueExpander
from .monitor_test import MonitorTest


class DockerContainerRunningTest(MonitorTest):
    """Alerts when a named container is not running, or does not exist.

    Docker being unreachable is not an alert - the exception travels up to
    `MonitorTest.run()`, which records it as an error.
    """

    test_type = "docker_container_running"

    def __init__(
        self,
        config: TestConfig,
        value_expander: ValueExpander,
        docker_factory: DockerServiceFactory,
    ):
        super().__init__(config, value_expander)
        self.docker_factory = docker_factory

    def container_names(self) -> list[str]:
        names: list[str] = []

        single = self.option("container_name")
        if single and str(single).strip():
            names.append(str(single).strip())

        listed = self.option("container_names") or []
        if isinstance(listed, str):
            listed = [listed]
        names.extend(str(n).strip() for n in listed if str(n).strip())

        # a container named in both settings is still one container
        names = list(dict.fromkeys(names))

        if not names:
            raise ValueError(
                f"Test '{self.key}' (docker_container_running) requires a "
                f"'container_name' or 'container_names' setting"
            )

        return names

    def execute(self, result: TestResult):
        names = self.container_names()
        require_healthy = bool(self.option("require_healthy", False))
        allow_restarting = bool(self.option("allow_restarting", False))

        service = self.docker_factory.create(self.option("connection"))

        running = []
        for index, name in enumerate(names):
            try:
                status = service.inspect(name)
            except DockerUnavailable as e:
                # the result becomes an ERROR, and an error must not also carry
                # the alerts raised for the containers checked before docker
                # went away - that would route one result to both the error and
                # the alert contacts
                result.alerts.clear()
                result.status = ResultStatus.OK
                result.message = None
                unchecked = names[index:]
                raise DockerUnavailable(
                    f"{e} - {len(unchecked)} of {len(names)} container(s) were "
                    f"not checked: {', '.join(unchecked)}"
                ) from e

            if status is None:
                self.add_alert(
                    result,
                    Alert(name=name, message=f"Container '{name}' does not exist"),
                )
                continue

            alert = self._check(status, name, require_healthy, allow_restarting)
            if alert is None:
                running.append(name)
            else:
                self.add_alert(result, alert)

        result.value = f"{len(running)} of {len(names)} running"
        if result.status == ResultStatus.OK:
            result.message = f"All {len(running)} container(s) running"

    @staticmethod
    def _check(
        status: ContainerStatus,
        name: str,
        require_healthy: bool,
        allow_restarting: bool,
    ) -> Alert | None:
        """The alert this container's state warrants, None when it is fine."""
        if status.state == RESTARTING_STATE and allow_restarting:
            return None

        if not status.is_running:
            return Alert(
                name=name,
                message=f"Container '{name}' is not running (state: {status.state})",
                value=status.state,
                details=status.details(),
            )

        if require_healthy and status.is_unhealthy:
            return Alert(
                name=name,
                message=f"Container '{name}' is running but unhealthy",
                value=status.health,
                details=status.details(),
            )

        return None
