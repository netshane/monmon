# docker container running

- Add a new test to check if a docker container is running.

- If the docker container is not in the state of "running" or does not exist, the test should raise an ALERT.

- If docker is not installed or the docker daemon is not running, the test should raise an ERROR.

- the connection argument should use the named connection from the `[connections]` section of the monitor
- if no connection is specified, the test should default to using the localhost

- Update docs and implement unit tests for the new test.
- Add a sample entry to `monitors/sample.toml` for the new test.

```toml
[connections]
docker_remote = {docker_host = "ssh://user@remotehost:22"}
docker_local = {docker_host = "unix:///var/run/docker.sock"}

[test.docker_container_running]
# Checks if a specific Docker instance is running
key = "network.appgroup.app.docker"  
connection = "docker_remote" # this should create a connection using the dependencies method get_docker and should default to localhost if not defined
container_name = "my_container" # the name of the Docker container to check
container_names = [
    "my_container1",
    "my_container2",
] # a list of container names to check, only passes if all containers are running
require_healthy = false            # [optional] alert when running but healthcheck is unhealthy, default is false
allow_restarting = false           # [optional] treat "restarting" as ok, default is false
```

## Connection transports

Only local (`unix://`, `npipe://`) and `ssh://` docker hosts are in scope.
`tcp://` is out of scope for now - the connection table is free to grow
`tls_ca` / `tls_cert` / `tls_key` keys later, so
`DockerServiceFactory.create()` should reject an unsupported scheme with a
clear message rather than guess at one.

`unix:///var/run/docker.sock` needs nothing beyond filesystem permission on
the socket - the account running `run-scheduled` must be in the `docker`
group (or be root).  `npipe:////./pipe/docker_engine` is the same thing on
windows, where `run-scheduled` runs from Task Scheduler; the account needs
access to the docker engine pipe.  A test that names no connection gets
whichever of the two suits the platform it is running on.

## Operational requirements for `ssh://` connections

`ssh://` carries no TLS material, but it has prerequisites that must hold in a
non-interactive cron / Task Scheduler environment:

- **`paramiko` must be installed.**  The docker sdk only uses the ssh
  transport when it is available; add it via the `docker[ssh]` extra in
  `pyproject.toml`.
- **The ssh key must be usable without a prompt.**  Either passphrase-less, or
  loaded into an agent that the scheduled process can actually reach - a cron
  job has no agent by default.
- **The remote host must already be in `known_hosts`** for the account running
  monmon.  The first connection to an unknown host fails host key
  verification, so `known_hosts` has to be pre-seeded.  Disabling host key
  checking is not an acceptable workaround.
- **The remote `user` must be in the remote host's `docker` group.**

All of these fail as connection errors, so they map to `ResultStatus.ERROR`
alongside "docker is not installed" and "the daemon is not running".  The
`DockerUnavailable` mapping must therefore catch paramiko's exceptions as well
as the docker sdk's - an ssh auth or host key failure must not escape as an
unhandled exception or be reported as a container alert.

## Implementation summary

Claude session: `session_01BP7epMGpjee7w5DmMLcVux`

Implemented as a new test type following the "add a monitor test type" recipe
in `docs/architecture.md`, plus the connection plumbing that recipe does not
cover.

**New files**

- `locallib/docker_service.py` - `DockerService` (one docker host, client built
  lazily, `inspect()` returning a `ContainerStatus` or `None` when no such
  container exists) and `DockerServiceFactory` (named connections with the
  `add_connections` / `connections_snapshot` / `restore_connections` contract
  the runner uses).  The docker sdk is imported lazily, so a missing package is
  a `DockerUnavailable` like a missing daemon is.  `tcp://`, `http://` and
  `https://` hosts are rejected with a message pointing at the missing tls
  material; any other scheme is rejected as unsupported.
- `locallib/monitor_tests/docker_tests.py` - `DockerContainerRunningTest`.
  Container names come from `container_name` and/or `container_names`
  (ping style).  A missing container, a state other than `running`, and -
  under `require_healthy` - a running but `unhealthy` container each raise one
  alert; `allow_restarting` makes `restarting` count as running.
  `DockerUnavailable` is deliberately not caught, so `MonitorTest.run()`
  records it as an ERROR.
- `tests/unit/test_docker_service.py` - connection resolution, scheme
  rejection, snapshot/restore, and `inspect` attribute parsing.

**Changed**

- `locallib/monitor_tests/__init__.py`, `locallib/monitor_test_factory.py` -
  export and register `docker_container_running` behind a new `_docker()`
  dependency helper.
- `locallib/monitor_runner.py` - takes a `docker_factory`, snapshots and
  restores it alongside the others, and routes a `[connections]` table with a
  `docker_host` key to it.  Any other table still goes to opensearch, so
  existing monitors are unaffected.
- `locallib/dependencies.py` - `get_docker_service_factory()`, a `[docker]`
  settings section (`host`, `timeout_seconds`), and the wiring into
  `get_monitor_test_factory()` / `get_monitor_runner()`.
- `pyproject.toml` - `docker[ssh]>=7.1.0`.
- `settings.toml`, `monitors/sample.toml`, `docs/monitors.md`, `README.md` -
  the `[docker]` defaults, the sample connections and test section, the
  `[connections]` docker table form with the `ssh://` prerequisites, and the
  test type's row and option table.
- `tests/unit/test_monitor_tests.py`, `tests/unit/test_monitor_runner.py`,
  `tests/unit/test_monitor_service.py` - eight cases for the new test, the
  docker connection routing, and the new constructor argument.

**Windows support** - `npipe://` is a supported scheme, and the default host
for a test that names no connection is chosen by platform
(`docker_service.local_docker_host()`), so a monitor running from Task
Scheduler reaches the local engine without configuration.  `[docker] host` in
`settings.toml` overrides it.

**Post review fixes** - the `[docker] host` default is scheme checked like a
named connection is; the factory reuses one `DockerService` per host so a
monitor with several docker tests opens one connection rather than one each;
a `DockerUnavailable` part way through a container list discards the alerts
already raised (an error result must not also reach the alert contacts) and
names the containers it did not get to; `container_name` is trimmed and
de-duplicated against `container_names`.

**Verified** - 524 unit tests pass; `uvx ruff format` and `uvx ruff check` are
clean.  The `DockerUnavailable` -> ERROR path was also exercised against the
real docker sdk.  No docker daemon was reachable on the development machine,
so the live "container is running" and "container not found" paths are covered
by unit tests with fakes rather than against a real daemon.
