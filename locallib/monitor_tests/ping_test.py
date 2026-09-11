"""Ping test: alerts when any listed server cannot be reached."""

from __future__ import annotations

from ..monitor_models import TestConfig
from ..monitor_results import Alert, ResultStatus, TestResult
from ..ping_client import PingClient
from ..value_expander import ValueExpander
from .monitor_test import MonitorTest


class PingTest(MonitorTest):
    test_type = "ping"

    def __init__(
        self,
        config: TestConfig,
        value_expander: ValueExpander,
        ping_client: PingClient,
    ):
        super().__init__(config, value_expander)
        self.ping_client = ping_client

    def servers(self) -> list[str]:
        servers: list[str] = []

        single = self.option("server")
        if single:
            servers.append(str(single))

        listed = self.option("servers") or []
        if isinstance(listed, str):
            listed = [listed]
        servers.extend(str(s) for s in listed if str(s).strip())

        if not servers:
            raise ValueError(
                f"Test '{self.key}' (ping) requires a 'server' or 'servers' setting"
            )

        return servers

    def execute(self, result: TestResult):
        reachable = []

        for server in self.servers():
            outcome = self.ping_client.ping(server)
            if outcome.reachable:
                reachable.append(server)
                continue

            self.add_alert(
                result,
                Alert(
                    name=server,
                    message=f"{server} could not be reached",
                    details=outcome.output or None,
                ),
            )

        result.value = f"{len(reachable)} reachable"
        if result.status == ResultStatus.OK:
            result.message = f"All {len(reachable)} server(s) reachable"
