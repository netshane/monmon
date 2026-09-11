"""Thin wrapper around the system ping command so tests can mock it."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass


@dataclass
class PingOutcome:
    server: str
    reachable: bool
    output: str = ""


class PingClient:
    def __init__(self, count: int = 2, timeout_seconds: int = 5):
        self.count = count
        self.timeout_seconds = timeout_seconds

    def build_command(self, server: str) -> list[str]:
        if platform.system().lower() == "windows":
            return [
                "ping",
                "-n",
                str(self.count),
                "-w",
                str(self.timeout_seconds * 1000),
                server,
            ]

        return ["ping", "-c", str(self.count), "-W", str(self.timeout_seconds), server]

    def ping(self, server: str) -> PingOutcome:
        command = self.build_command(server)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds * self.count + 5,
            )
        except subprocess.TimeoutExpired:
            return PingOutcome(server=server, reachable=False, output="ping timed out")
        except FileNotFoundError as e:
            return PingOutcome(server=server, reachable=False, output=str(e))

        output = (completed.stdout or "") + (completed.stderr or "")
        return PingOutcome(
            server=server,
            reachable=completed.returncode == 0,
            output=output.strip(),
        )
