import pytest

from locallib.monitor_models import MonitorDefinition, TestConfig
from locallib.monitor_results import Alert, ResultStatus, TestResult
from locallib.monitor_runner import MonitorRunner

"""
Test: running all of the tests in a monitor
"""


class StubTest:
    def __init__(self, result: TestResult):
        self.result = result
        self.ran = False

    def run(self) -> TestResult:
        self.ran = True
        return self.result


class StubTestFactory:
    """Returns a canned test per test key, or raises for unknown keys."""

    def __init__(self, results: dict[str, TestResult], unknown: set[str] | None = None):
        self.results = results
        self.unknown = unknown or set()
        self.created: list[str] = []

    def create(self, config: TestConfig):
        self.created.append(config.key)
        if config.key in self.unknown:
            raise ValueError(f"Unknown test type '{config.test_type}'")

        return StubTest(self.results[config.key])


class RecordingFactory:
    def __init__(self):
        self.connections = {}

    def add_connections(self, new_connections: dict):
        self.connections.update(new_connections)

    def connections_snapshot(self) -> dict:
        return self.connections.copy()

    def restore_connections(self, snapshot: dict):
        self.connections = dict(snapshot)


def _definition(tests: list[TestConfig], connections: dict | None = None):
    return MonitorDefinition(
        name="xyz",
        source_file="xyz.toml",
        tests=tests,
        connections=connections or {},
    )


def _runner(factory, simulate: bool = False):
    return MonitorRunner(
        test_factory=factory,
        db_factory=RecordingFactory(),
        docker_factory=RecordingFactory(),
        opensearch_factory=RecordingFactory(),
        simulate=simulate,
    )


@pytest.mark.unit
def test_all_tests_run_and_ok_rolls_up_to_ok():
    tests = [TestConfig("ping", "a"), TestConfig("dbflag", "b")]
    factory = StubTestFactory(
        {
            "a": TestResult(key="a", test_type="ping"),
            "b": TestResult(key="b", test_type="dbflag"),
        }
    )

    result = _runner(factory).run(_definition(tests))

    assert factory.created == ["a", "b"]
    assert result.status == ResultStatus.OK
    assert len(result.test_results) == 2
    assert result.finished_at is not None


@pytest.mark.unit
def test_an_alerting_test_makes_the_monitor_alert():
    tests = [TestConfig("ping", "a"), TestConfig("dbflag", "b")]
    factory = StubTestFactory(
        {
            "a": TestResult(key="a", test_type="ping"),
            "b": TestResult(
                key="b",
                test_type="dbflag",
                status=ResultStatus.ALERT,
                alerts=[Alert(message="too many")],
            ),
        }
    )

    result = _runner(factory).run(_definition(tests))

    assert result.status == ResultStatus.ALERT
    assert [a.message for a in result.alerts] == ["too many"]


@pytest.mark.unit
def test_errors_outrank_alerts():
    tests = [TestConfig("ping", "a"), TestConfig("dbflag", "b")]
    factory = StubTestFactory(
        {
            "a": TestResult(
                key="a",
                test_type="ping",
                status=ResultStatus.ALERT,
                alerts=[Alert(message="down")],
            ),
            "b": TestResult(
                key="b", test_type="dbflag", status=ResultStatus.ERROR, error="boom"
            ),
        }
    )

    result = _runner(factory).run(_definition(tests))

    assert result.status == ResultStatus.ERROR
    assert result.errors == ["boom"]


@pytest.mark.unit
def test_a_test_that_cannot_be_created_is_an_error_not_a_crash():
    tests = [TestConfig("nonsense", "a")]
    factory = StubTestFactory({}, unknown={"a"})

    result = _runner(factory).run(_definition(tests))

    assert result.status == ResultStatus.ERROR
    assert "Unknown test type" in result.test_results[0].error


@pytest.mark.unit
def test_simulate_skips_execution():
    tests = [TestConfig("ping", "a")]
    factory = StubTestFactory({"a": TestResult(key="a", test_type="ping")})

    result = _runner(factory, simulate=True).run(_definition(tests))

    assert result.status == ResultStatus.SKIPPED
    assert result.test_results[0].message == "Skipped - simulate mode"


@pytest.mark.unit
def test_monitor_connections_are_registered_with_the_factories_during_the_run():
    db_factory = RecordingFactory()
    docker_factory = RecordingFactory()
    opensearch_factory = RecordingFactory()
    seen = {}

    class SnapshottingTestFactory:
        def create(self, config: TestConfig):
            seen["db"] = dict(db_factory.connections)
            seen["docker"] = dict(docker_factory.connections)
            seen["opensearch"] = dict(opensearch_factory.connections)
            return StubTest(TestResult(key=config.key, test_type=config.test_type))

    runner = MonitorRunner(
        test_factory=SnapshottingTestFactory(),
        db_factory=db_factory,
        docker_factory=docker_factory,
        opensearch_factory=opensearch_factory,
    )
    definition = _definition(
        [TestConfig("ping", "a")],
        connections={
            "db_local": "sqlite:///local.db",
            "es_local": {"host": "localhost", "default_index": "logs*"},
            "docker_local": {"docker_host": "unix:///var/run/docker.sock"},
            "aws_dev": "",
        },
    )

    runner.run(definition)

    assert seen["db"] == {"db_local": "sqlite:///local.db"}
    assert seen["docker"] == {
        "docker_local": {"docker_host": "unix:///var/run/docker.sock"}
    }
    assert seen["opensearch"] == {
        "es_local": {"host": "localhost", "default_index": "logs*"}
    }


@pytest.mark.unit
def test_monitor_connections_are_restored_after_the_run_so_they_do_not_leak():
    factory = StubTestFactory({})
    runner = _runner(factory)
    definition = _definition(
        [],
        connections={
            "db_local": "sqlite:///local.db",
            "docker_local": {"docker_host": "unix:///var/run/docker.sock"},
        },
    )

    runner.run(definition)

    assert runner.db_factory.connections == {}
    assert runner.docker_factory.connections == {}
    assert runner.opensearch_factory.connections == {}


@pytest.mark.unit
def test_forced_hierarchy_alerts_are_added_to_the_result():
    factory = StubTestFactory({"a": TestResult(key="a", test_type="ping")})

    result = _runner(factory).run(
        _definition([TestConfig("ping", "a")]),
        forced_alerts=[Alert(message="parent failed")],
    )

    assert result.status == ResultStatus.ALERT
    assert [a.message for a in result.alerts] == ["parent failed"]


@pytest.mark.unit
def test_a_monitor_with_no_tests_is_reported():
    result = _runner(StubTestFactory({})).run(_definition([]))

    assert result.status == ResultStatus.OK
    assert result.message == "Monitor has no tests"
