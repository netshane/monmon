"""Docker connections and container inspection for the docker tests.

Only local (`unix://`, `npipe://`) and `ssh://` hosts are supported.  `tcp://`
is deliberately rejected rather than guessed at - it needs tls material
(`ca.pem`, `cert.pem`, `key.pem`) that the connection table does not carry yet.

Everything that means "docker itself could not be reached" - the package or
the daemon missing, an ssh transport that will not connect - is raised as
`DockerUnavailable` so the tests report it as an error rather than as an alert
about a container.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass

from loguru import logger

# used when a test names no connection - "the localhost docker".  The local
# engine is a socket on linux / macos and a named pipe on windows
DEFAULT_UNIX_DOCKER_HOST = "unix:///var/run/docker.sock"
DEFAULT_WINDOWS_DOCKER_HOST = "npipe:////./pipe/docker_engine"

SUPPORTED_SCHEMES = ("unix://", "npipe://", "ssh://")


def local_docker_host() -> str:
    """The local docker engine for the platform this is running on."""
    if platform.system().lower() == "windows":
        return DEFAULT_WINDOWS_DOCKER_HOST

    return DEFAULT_UNIX_DOCKER_HOST


RUNNING_STATE = "running"
RESTARTING_STATE = "restarting"
UNHEALTHY_HEALTH = "unhealthy"


class DockerUnavailable(Exception):
    """Docker could not be reached at all.

    Distinct from "the container is not running" - this is an error with the
    monitoring, not a finding about the container.
    """


@dataclass
class ContainerStatus:
    """The parts of `docker inspect` the tests care about."""

    name: str
    state: str
    health: str | None = None
    exit_code: int | None = None
    started_at: str | None = None
    restart_count: int | None = None

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING_STATE

    @property
    def is_unhealthy(self) -> bool:
        return self.health == UNHEALTHY_HEALTH

    def details(self) -> str:
        parts = [f"state: {self.state}"]
        if self.health:
            parts.append(f"health: {self.health}")
        if self.exit_code is not None and not self.is_running:
            parts.append(f"exit code: {self.exit_code}")
        if self.started_at:
            parts.append(f"started at: {self.started_at}")
        if self.restart_count:
            parts.append(f"restart count: {self.restart_count}")

        return "\n".join(parts)


def _docker_modules():
    """Import the docker sdk lazily.

    A missing package is a `DockerUnavailable` like a missing daemon is, and
    importing here keeps the rest of the module usable without it.
    """
    try:
        import docker
        import docker.errors

        return docker, docker.errors
    except ImportError as e:
        raise DockerUnavailable(
            f"the docker python package is not installed: {e}"
        ) from e


class DockerService:
    """One docker host.  The client is built on first use."""

    def __init__(self, docker_host: str, timeout_seconds: int = 30):
        self.docker_host = docker_host
        self.timeout_seconds = timeout_seconds
        self.client = None

    def __get_client(self):
        if self.client is None:
            docker, errors = _docker_modules()
            try:
                self.client = docker.DockerClient(
                    base_url=self.docker_host,
                    timeout=self.timeout_seconds,
                )
            except errors.DockerException as e:
                raise DockerUnavailable(
                    f"could not connect to docker at '{self.docker_host}': {e}"
                ) from e
            except Exception as e:
                # ssh transport failures (paramiko auth, unknown host key)
                # surface as their own exception types
                raise DockerUnavailable(
                    f"could not connect to docker at '{self.docker_host}': "
                    f"{type(e).__name__}: {e}"
                ) from e

        return self.client

    def inspect(self, container_name: str) -> ContainerStatus | None:
        """The container's status, or None when no such container exists."""
        _docker, errors = _docker_modules()
        client = self.__get_client()

        try:
            container = client.containers.get(container_name)
        except errors.NotFound:
            return None
        except Exception as e:
            raise DockerUnavailable(
                f"could not inspect container '{container_name}' on "
                f"'{self.docker_host}': {type(e).__name__}: {e}"
            ) from e

        return self.status_from_attrs(container_name, container.attrs or {})

    @staticmethod
    def status_from_attrs(container_name: str, attrs: dict) -> ContainerStatus:
        state = attrs.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        name = str(attrs.get("Name") or container_name).lstrip("/")

        return ContainerStatus(
            name=name,
            state=str(state.get("Status") or "unknown"),
            health=str(health) if health else None,
            exit_code=state.get("ExitCode"),
            started_at=state.get("StartedAt") or None,
            restart_count=attrs.get("RestartCount"),
        )


class DockerServiceFactory:
    """Named docker connections, with the snapshot/restore contract the runner
    uses to scope a monitor's `[connections]` overrides to its own run."""

    def __init__(
        self,
        connections: dict,
        default_docker_host: str | None = None,
        timeout_seconds: int = 30,
    ):
        self.connections = connections.copy()
        self.default_docker_host = default_docker_host or local_docker_host()
        self.timeout_seconds = timeout_seconds
        # one service - and so one client, and one ssh session - per host,
        # however many test sections in a monitor use it
        self.services: dict[str, DockerService] = {}

    def add_connections(self, new_connections: dict):
        self.connections.update(new_connections)

    def connections_snapshot(self) -> dict:
        return self.connections.copy()

    def restore_connections(self, snapshot: dict):
        self.connections = dict(snapshot)

    def create(self, connection: str | None = None) -> DockerService:
        docker_host = self._docker_host(connection)
        if docker_host not in self.services:
            self.services[docker_host] = DockerService(
                docker_host=docker_host,
                timeout_seconds=self.timeout_seconds,
            )

        return self.services[docker_host]

    def _docker_host(self, connection: str | None) -> str:
        if not connection or not str(connection).strip():
            logger.debug(
                f"No docker connection named - using {self.default_docker_host}"
            )
            return self._checked_scheme("[docker] host", self.default_docker_host)

        name = str(connection).strip()
        conn = self.connections.get(name)
        docker_host = conn.get("docker_host") if isinstance(conn, dict) else None
        if not docker_host or not str(docker_host).strip():
            raise ValueError(f"No docker connection found for '{name}'")

        return self._checked_scheme(name, str(docker_host).strip())

    @staticmethod
    def _checked_scheme(name: str, docker_host: str) -> str:
        """The host, if its scheme is one we support.

        Applied to the default host as well as to a named connection, so a
        `[docker] host` that cannot work is reported the same way rather than
        failing later inside the docker sdk.
        """
        if docker_host.startswith(SUPPORTED_SCHEMES):
            return docker_host

        if docker_host.startswith(("tcp://", "http://", "https://")):
            raise ValueError(
                f"Docker connection '{name}' uses '{docker_host}' - tcp docker "
                f"hosts are not supported, use {', '.join(SUPPORTED_SCHEMES)}"
            )

        raise ValueError(
            f"Docker connection '{name}' has an unsupported docker_host "
            f"'{docker_host}' - expected one of {', '.join(SUPPORTED_SCHEMES)}"
        )
