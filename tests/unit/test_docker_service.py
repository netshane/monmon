import pytest

from locallib.docker_service import (
    DEFAULT_UNIX_DOCKER_HOST,
    DEFAULT_WINDOWS_DOCKER_HOST,
    ContainerStatus,
    DockerService,
    DockerServiceFactory,
    local_docker_host,
)

"""
Test: docker connection resolution and inspect parsing

The docker sdk is never reached - these cover the connection table and the
attribute parsing only.
"""


def _factory(**kwargs) -> DockerServiceFactory:
    connections = {
        "docker_local": {"docker_host": "unix:///var/run/docker.sock"},
        "docker_remote": {"docker_host": "ssh://user@remotehost:22"},
        "docker_tcp": {"docker_host": "tcp://dockerhost:2376"},
        "docker_windows": {"docker_host": "npipe:////./pipe/docker_engine"},
        "db_prd": "sqlite:///local.db",
        "es_prd": {"host": "search.example.com", "default_index": "logs*"},
    }
    return DockerServiceFactory(connections=connections, **kwargs)


@pytest.mark.unit
def test_a_named_connection_supplies_its_docker_host():
    service = _factory().create("docker_remote")

    assert service.docker_host == "ssh://user@remotehost:22"


@pytest.mark.unit
def test_no_connection_falls_back_to_the_default_host():
    assert _factory().create().docker_host == local_docker_host()
    assert _factory().create("  ").docker_host == local_docker_host()
    assert (
        _factory(default_docker_host="ssh://user@build:22").create().docker_host
        == "ssh://user@build:22"
    )


@pytest.mark.unit
def test_the_local_engine_is_a_named_pipe_on_windows_and_a_socket_elsewhere(
    monkeypatch,
):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert local_docker_host() == DEFAULT_WINDOWS_DOCKER_HOST
    assert DockerServiceFactory({}).create().docker_host == DEFAULT_WINDOWS_DOCKER_HOST

    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert local_docker_host() == DEFAULT_UNIX_DOCKER_HOST
    assert DockerServiceFactory({}).create().docker_host == DEFAULT_UNIX_DOCKER_HOST


@pytest.mark.unit
def test_a_windows_named_pipe_connection_is_accepted():
    service = _factory().create("docker_windows")

    assert service.docker_host == "npipe:////./pipe/docker_engine"


@pytest.mark.unit
def test_an_unsupported_default_host_is_rejected_too():
    factory = _factory(default_docker_host="tcp://dockerhost:2376")

    with pytest.raises(ValueError, match="tcp docker hosts are not supported"):
        factory.create()


@pytest.mark.unit
def test_one_service_is_reused_per_host_so_a_monitor_opens_one_connection():
    factory = _factory()

    assert factory.create("docker_remote") is factory.create("docker_remote")
    assert factory.create("docker_local") is not factory.create("docker_remote")


@pytest.mark.unit
def test_an_unknown_or_non_docker_connection_is_rejected():
    factory = _factory()

    for name in ("nope", "db_prd", "es_prd"):
        with pytest.raises(ValueError, match="No docker connection found"):
            factory.create(name)


@pytest.mark.unit
def test_a_tcp_host_is_rejected_because_it_needs_tls_material():
    with pytest.raises(ValueError, match="tcp docker hosts are not supported"):
        _factory().create("docker_tcp")


@pytest.mark.unit
def test_an_unrecognised_scheme_is_rejected():
    factory = DockerServiceFactory(
        connections={"odd": {"docker_host": "carrier-pigeon://somewhere"}}
    )

    with pytest.raises(ValueError, match="unsupported docker_host"):
        factory.create("odd")


@pytest.mark.unit
def test_connections_snapshot_and_restore_scope_overrides_to_one_run():
    factory = DockerServiceFactory(connections={})
    snapshot = factory.connections_snapshot()

    factory.add_connections({"docker_temp": {"docker_host": "ssh://user@host:22"}})
    assert factory.create("docker_temp").docker_host == "ssh://user@host:22"

    factory.restore_connections(snapshot)
    with pytest.raises(ValueError):
        factory.create("docker_temp")


@pytest.mark.unit
def test_inspect_attributes_are_parsed_into_a_container_status():
    status = DockerService.status_from_attrs(
        "app",
        {
            "Name": "/app",
            "RestartCount": 2,
            "State": {
                "Status": "running",
                "ExitCode": 0,
                "StartedAt": "2026-08-26T10:00:00Z",
                "Health": {"Status": "unhealthy"},
            },
        },
    )

    assert status == ContainerStatus(
        name="app",
        state="running",
        health="unhealthy",
        exit_code=0,
        started_at="2026-08-26T10:00:00Z",
        restart_count=2,
    )
    assert status.is_running
    assert status.is_unhealthy


@pytest.mark.unit
def test_missing_attributes_do_not_break_the_status():
    status = DockerService.status_from_attrs("app", {})

    assert status.name == "app"
    assert status.state == "unknown"
    assert status.health is None
    assert status.details() == "state: unknown"


@pytest.mark.unit
def test_details_report_the_exit_code_of_a_stopped_container():
    status = DockerService.status_from_attrs(
        "app",
        {"RestartCount": 4, "State": {"Status": "exited", "ExitCode": 137}},
    )

    assert not status.is_running
    assert "exit code: 137" in status.details()
    assert "restart count: 4" in status.details()
