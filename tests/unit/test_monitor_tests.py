from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from locallib.custom_test_loader import CustomTestLoader
from locallib.docker_service import ContainerStatus, DockerUnavailable
from locallib.http_client import HttpResponse
from locallib.json_extractor import JsonExtractor
from locallib.monitor_models import TestConfig
from locallib.monitor_results import ResultStatus
from locallib.monitor_tests import (
    CustomTest,
    DbFlagTest,
    DbNoRowsTest,
    DbReportTest,
    DbThresholdTest,
    DockerContainerRunningTest,
    HtmlJsonExistsTest,
    HtmlJsonReportTest,
    HtmlJsonValueTest,
    HtmlStatusTest,
    OpenSearchFlagTest,
    OpenSearchReportTest,
    PingTest,
    SsaJobErrorsTest,
    SsaJobSucceededTest,
)
from locallib.ping_client import PingOutcome
from locallib.value_expander import ValueExpander

"""
Test: individual monitor test types

Every external dependency is faked so these stay offline unit tests.
"""

NOW = datetime(2026, 8, 12, 10, 30, 0)


@pytest.fixture()
def expander():
    return ValueExpander(now_provider=lambda: NOW)


@pytest.fixture()
def extractor():
    return JsonExtractor()


class FakePingClient:
    def __init__(self, reachable: dict):
        self.reachable = reachable
        self.pinged = []

    def ping(self, server: str) -> PingOutcome:
        self.pinged.append(server)
        ok = self.reachable.get(server, True)
        return PingOutcome(server=server, reachable=ok, output="" if ok else "no reply")


class FakeDockerService:
    def __init__(
        self,
        containers: dict,
        unavailable: str | None = None,
        unavailable_from: str | None = None,
    ):
        self.containers = containers
        self.unavailable = unavailable
        self.unavailable_from = unavailable_from
        self.inspected = []

    def inspect(self, container_name: str):
        if self.unavailable and self.unavailable_from in (None, container_name):
            raise DockerUnavailable(self.unavailable)

        self.inspected.append(container_name)
        return self.containers.get(container_name)


class FakeDockerFactory:
    def __init__(
        self,
        containers: dict,
        unavailable: str | None = None,
        unavailable_from: str | None = None,
    ):
        self.service = FakeDockerService(containers, unavailable, unavailable_from)
        self.requested = []

    def create(self, connection=None):
        self.requested.append(connection)
        return self.service


def _container(state: str = "running", **kwargs) -> ContainerStatus:
    return ContainerStatus(name=kwargs.pop("name", "app"), state=state, **kwargs)


class FakeDbFactory:
    """Serves an in memory sqlite engine seeded by the test."""

    def __init__(self, setup_sql: list[str]):
        self.engine = create_engine("sqlite://")
        self.requested = []
        with self.engine.begin() as conn:
            for statement in setup_sql:
                conn.execute(text(statement))

    def create(self, connection: str):
        self.requested.append(connection)
        return self.engine


class FakeSsaCursor:
    def __init__(self, columns: list[str], rows: list[list]):
        self.columns = columns
        self.rows = rows

    def keys(self):
        return self.columns

    def fetchall(self):
        return self.rows


class FakeSsaConnection:
    def __init__(self, columns: list[str], rows: list[list]):
        self.columns = columns
        self.rows = rows
        self.executed = None

    def execute(self, statement, params=None):
        self.executed = (str(statement), params)
        return FakeSsaCursor(self.columns, self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSsaEngine:
    def __init__(self, connection: FakeSsaConnection):
        self.connection = connection

    def connect(self):
        return self.connection


class FakeSsaDbFactory:
    """Stands in for a SQL Server connection; msdb queries can't run on sqlite."""

    def __init__(self, columns: list[str], rows: list[list]):
        self.connection = FakeSsaConnection(columns, rows)
        self.engine = FakeSsaEngine(self.connection)
        self.requested = []

    def create(self, connection: str):
        self.requested.append(connection)
        return self.engine


class FakeOpenSearchService:
    def __init__(self, response):
        self.response = response
        self.queries = []

    def query(self, query: str, index: str | None = None):
        self.queries.append((query, index))
        return self.response


class FakeOpenSearchFactory:
    def __init__(self, response):
        self.service = FakeOpenSearchService(response)
        self.requested = []

    def create(self, connection: str):
        self.requested.append(connection)
        return self.service


class FakeHttpClient:
    def __init__(self, status_code: int = 200, body: str = "{}"):
        self.status_code = status_code
        self.body = body
        self.requested = []

    def get(
        self,
        url: str,
        headers: dict | None = None,
        verify_ssl: bool | None = None,
        ca_bundle: str | None = None,
    ) -> HttpResponse:
        self.requested.append(url)
        self.verify_ssl = verify_ssl
        self.ca_bundle = ca_bundle
        return HttpResponse(url=url, status_code=self.status_code, text=self.body)


# -- ping ----------------------------------------------------------------


@pytest.mark.unit
def test_ping_passes_when_all_servers_respond(expander):
    config = TestConfig(
        test_type="ping",
        key="net.ping",
        options={"server": "example.com", "servers": ["1.1.1.1"]},
    )
    client = FakePingClient({})

    result = PingTest(config, expander, client).run()

    assert result.status == ResultStatus.OK
    assert client.pinged == ["example.com", "1.1.1.1"]


@pytest.mark.unit
def test_ping_alerts_for_each_unreachable_server(expander):
    config = TestConfig(
        test_type="ping",
        key="net.ping",
        options={"servers": ["up.com", "down.com"]},
    )
    client = FakePingClient({"down.com": False})

    result = PingTest(config, expander, client).run()

    assert result.status == ResultStatus.ALERT
    assert [a.name for a in result.alerts] == ["down.com"]
    assert result.alerts[0].details == "no reply"


@pytest.mark.unit
def test_ping_without_servers_is_an_error(expander):
    result = PingTest(
        TestConfig("ping", "net.ping", {}), expander, FakePingClient({})
    ).run()

    assert result.status == ResultStatus.ERROR
    assert "server" in result.error


# -- docker --------------------------------------------------------------


def _docker_test(options: dict, factory, expander):
    config = TestConfig(
        test_type="docker_container_running",
        key="net.app.docker",
        options=options,
    )
    return DockerContainerRunningTest(config, expander, factory).run()


@pytest.mark.unit
def test_docker_passes_when_every_container_is_running(expander):
    factory = FakeDockerFactory(
        {"app": _container(), "db": _container()},
    )

    result = _docker_test(
        {
            "connection": "docker_remote",
            "container_name": "app",
            "container_names": ["db"],
        },
        factory,
        expander,
    )

    assert result.status == ResultStatus.OK
    assert result.value == "2 of 2 running"
    assert factory.service.inspected == ["app", "db"]
    assert factory.requested == ["docker_remote"]


@pytest.mark.unit
def test_docker_alerts_when_a_container_is_not_running(expander):
    factory = FakeDockerFactory(
        {"app": _container("exited", exit_code=137, restart_count=3)},
    )

    result = _docker_test({"container_name": "app"}, factory, expander)

    assert result.status == ResultStatus.ALERT
    assert [a.name for a in result.alerts] == ["app"]
    assert "not running (state: exited)" in result.alerts[0].message
    assert "exit code: 137" in result.alerts[0].details
    assert result.value == "0 of 1 running"


@pytest.mark.unit
def test_docker_alerts_when_a_container_does_not_exist(expander):
    factory = FakeDockerFactory({})

    result = _docker_test({"container_names": ["missing"]}, factory, expander)

    assert result.status == ResultStatus.ALERT
    assert "does not exist" in result.alerts[0].message


@pytest.mark.unit
def test_docker_without_a_connection_uses_the_default_host(expander):
    factory = FakeDockerFactory({"app": _container()})

    result = _docker_test({"container_name": "app"}, factory, expander)

    assert result.status == ResultStatus.OK
    assert factory.requested == [None]


@pytest.mark.unit
def test_docker_ignores_health_unless_require_healthy_is_set(expander):
    factory = FakeDockerFactory({"app": _container(health="unhealthy")})

    assert _docker_test({"container_name": "app"}, factory, expander).status == (
        ResultStatus.OK
    )

    result = _docker_test(
        {"container_name": "app", "require_healthy": True}, factory, expander
    )

    assert result.status == ResultStatus.ALERT
    assert "unhealthy" in result.alerts[0].message


@pytest.mark.unit
def test_docker_restarting_alerts_unless_allow_restarting_is_set(expander):
    factory = FakeDockerFactory({"app": _container("restarting")})

    assert _docker_test({"container_name": "app"}, factory, expander).status == (
        ResultStatus.ALERT
    )

    result = _docker_test(
        {"container_name": "app", "allow_restarting": True}, factory, expander
    )

    assert result.status == ResultStatus.OK


@pytest.mark.unit
def test_docker_unavailable_is_an_error_not_an_alert(expander):
    factory = FakeDockerFactory({}, unavailable="daemon is not running")

    result = _docker_test({"container_name": "app"}, factory, expander)

    assert result.status == ResultStatus.ERROR
    assert "daemon is not running" in result.error
    assert result.alerts == []


@pytest.mark.unit
def test_docker_unavailable_part_way_through_discards_the_alerts_already_raised(
    expander,
):
    """An error must not also be routed to the alert contacts."""
    factory = FakeDockerFactory(
        {"app": _container("exited")},
        unavailable="daemon went away",
        unavailable_from="db",
    )

    result = _docker_test(
        {"container_names": ["app", "db", "cache"]}, factory, expander
    )

    assert result.status == ResultStatus.ERROR
    assert result.alerts == []
    assert "2 of 3 container(s) were not checked: db, cache" in result.error


@pytest.mark.unit
def test_docker_container_names_are_trimmed_and_de_duplicated(expander):
    factory = FakeDockerFactory({"app": _container()})

    result = _docker_test(
        {"container_name": " app ", "container_names": ["app"]}, factory, expander
    )

    assert result.status == ResultStatus.OK
    assert factory.service.inspected == ["app"]
    assert result.value == "1 of 1 running"


@pytest.mark.unit
def test_docker_with_a_blank_container_name_is_an_error(expander):
    result = _docker_test({"container_name": "   "}, FakeDockerFactory({}), expander)

    assert result.status == ResultStatus.ERROR
    assert "container_name" in result.error


@pytest.mark.unit
def test_docker_without_container_names_is_an_error(expander):
    result = _docker_test({}, FakeDockerFactory({}), expander)

    assert result.status == ResultStatus.ERROR
    assert "container_name" in result.error


# -- database ------------------------------------------------------------


@pytest.mark.unit
def test_dbflag_alerts_when_the_scalar_is_positive(expander):
    db = FakeDbFactory([])
    config = TestConfig(
        "dbflag", "db.flag", {"connection": "db_aws", "query": "SELECT 3"}
    )

    result = DbFlagTest(config, expander, db).run()

    assert result.status == ResultStatus.ALERT
    assert result.alerts[0].value == 3
    assert db.requested == ["db_aws"]


@pytest.mark.unit
def test_dbflag_passes_when_the_scalar_is_zero(expander):
    config = TestConfig(
        "dbflag", "db.flag", {"connection": "db_aws", "query": "SELECT 0"}
    )

    result = DbFlagTest(config, expander, FakeDbFactory([])).run()

    assert result.status == ResultStatus.OK
    assert result.alerts == []


@pytest.mark.unit
def test_dbflag_expands_tokens_in_the_query(expander):
    config = TestConfig(
        "dbflag",
        "db.flag",
        {
            "connection": "db_aws",
            "query": "SELECT 0 WHERE '{{>today<}}' = '2026-08-12'",
        },
    )

    result = DbFlagTest(config, expander, FakeDbFactory([])).run()

    assert result.status == ResultStatus.OK


@pytest.mark.unit
def test_dbthreshold_alerts_per_breaching_row(expander):
    db = FakeDbFactory(
        [
            "CREATE TABLE monitors (Name TEXT, Value REAL, Details TEXT)",
            "INSERT INTO monitors VALUES ('a', 9.9, 'busy'), ('b', 1.0, 'quiet')",
        ]
    )
    config = TestConfig(
        "dbthreshold",
        "db.threshold",
        {
            "connection": "db_aws",
            "threshold": 4.2,
            "query": "SELECT Name, Value, Details FROM monitors",
        },
    )

    result = DbThresholdTest(config, expander, db).run()

    assert result.status == ResultStatus.ALERT
    assert len(result.alerts) == 1
    alert = result.alerts[0]
    assert (alert.name, alert.value, alert.threshold, alert.details) == (
        "a",
        9.9,
        4.2,
        "busy",
    )


@pytest.mark.unit
def test_dbthreshold_requires_a_value_column(expander):
    db = FakeDbFactory(["CREATE TABLE t (Name TEXT)", "INSERT INTO t VALUES ('a')"])
    config = TestConfig(
        "dbthreshold",
        "db.threshold",
        {"connection": "db_aws", "threshold": 1, "query": "SELECT Name FROM t"},
    )

    result = DbThresholdTest(config, expander, db).run()

    assert result.status == ResultStatus.ERROR
    assert "Value" in result.error


@pytest.mark.unit
def test_dbnorows_alerts_with_every_returned_row(expander):
    db = FakeDbFactory(
        [
            "CREATE TABLE monitors (Name TEXT, Status TEXT)",
            "INSERT INTO monitors VALUES ('a', 'Failed'), ('b', NULL)",
        ]
    )
    config = TestConfig(
        "dbnorows",
        "db.norows",
        {"connection": "db_aws", "query": "SELECT Name, Status FROM monitors"},
    )

    result = DbNoRowsTest(config, expander, db).run()

    assert result.status == ResultStatus.ALERT
    assert result.value == 2
    assert result.message == "Query returned 2 row(s)"
    alert = result.alerts[0]
    assert alert.name == "db.norows"
    assert alert.value == 2
    assert alert.message == "Query returned 2 row(s)"
    assert alert.details == "Name | Status\na | Failed\nb | "


@pytest.mark.unit
def test_dbnorows_flattens_newlines_and_separators_in_values(expander):
    db = FakeDbFactory(
        [
            "CREATE TABLE monitors (Name TEXT, Status TEXT)",
            "INSERT INTO monitors VALUES ('a', 'line one" + chr(10) + "line | two')",
        ]
    )
    config = TestConfig(
        "dbnorows",
        "db.norows",
        {"connection": "db_aws", "query": "SELECT Name, Status FROM monitors"},
    )

    result = DbNoRowsTest(config, expander, db).run()

    details = result.alerts[0].details
    assert details == "Name | Status\na | line one line \\| two"
    assert len(details.splitlines()) == 2


@pytest.mark.unit
def test_dbnorows_caps_the_listed_rows(expander):
    db = FakeDbFactory(
        [
            "CREATE TABLE monitors (Name TEXT)",
            "INSERT INTO monitors VALUES ('a'), ('b'), ('c'), ('d')",
        ]
    )
    config = TestConfig(
        "dbnorows",
        "db.norows",
        {
            "connection": "db_aws",
            "max_rows": 2,
            "query": "SELECT Name FROM monitors ORDER BY Name",
        },
    )

    result = DbNoRowsTest(config, expander, db).run()

    assert result.value == 4
    assert result.alerts[0].value == 4
    assert result.alerts[0].details == "Name\na\nb\n... and 2 more row(s)"


@pytest.mark.unit
def test_dbnorows_passes_when_the_query_returns_nothing(expander):
    db = FakeDbFactory(["CREATE TABLE monitors (Name TEXT)"])
    config = TestConfig(
        "dbnorows",
        "db.norows",
        {"connection": "db_aws", "query": "SELECT Name FROM monitors"},
    )

    result = DbNoRowsTest(config, expander, db).run()

    assert result.status == ResultStatus.OK
    assert result.value == 0
    assert result.alerts == []
    assert result.message == "Query returned no rows"


@pytest.mark.unit
def test_dbreport_builds_a_table(expander):
    db = FakeDbFactory(
        [
            "CREATE TABLE monitors (Name TEXT, Status TEXT)",
            "INSERT INTO monitors VALUES ('a', 'Failed'), ('b', 'Passed')",
        ]
    )
    config = TestConfig(
        "dbreport",
        "db.report",
        {"connection": "db_aws", "query": "SELECT Name, Status FROM monitors"},
    )

    result = DbReportTest(config, expander, db).run()

    assert result.status == ResultStatus.OK
    report = result.reports[0]
    assert report.columns == ["Name", "Status"]
    assert report.rows == [["a", "Failed"], ["b", "Passed"]]


@pytest.mark.unit
def test_dbreport_suppresses_an_empty_report_by_default(expander):
    db = FakeDbFactory(["CREATE TABLE monitors (Name TEXT, Status TEXT)"])
    config = TestConfig(
        "dbreport",
        "db.report",
        {"connection": "db_aws", "query": "SELECT Name, Status FROM monitors"},
    )

    result = DbReportTest(config, expander, db).run()

    assert result.reports == []
    assert result.value == 0
    assert result.message is None


@pytest.mark.unit
def test_dbreport_notify_if_empty_sends_an_empty_report(expander):
    db = FakeDbFactory(["CREATE TABLE monitors (Name TEXT, Status TEXT)"])
    config = TestConfig(
        "dbreport",
        "db.report",
        {
            "connection": "db_aws",
            "query": "SELECT Name, Status FROM monitors",
            "notify_if_empty": True,
        },
    )

    result = DbReportTest(config, expander, db).run()

    assert result.value == 0
    report = result.reports[0]
    assert report.columns == ["Name", "Status"]
    assert report.rows == []


# -- ssa job tests ---------------------------------------------------------


@pytest.mark.unit
def test_ssa_job_succeeded_passes_when_last_run_succeeded(expander):
    db = FakeSsaDbFactory(
        ["RunStatusDescription"],
        [["Succeeded"]],
    )
    config = TestConfig(
        "ssa_job_succeeded",
        "aws_db.MyJobName.succeeded",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    result = SsaJobSucceededTest(config, expander, db).run()

    assert result.status == ResultStatus.OK
    assert result.value == "Succeeded"
    assert db.requested == ["db_aws"]


@pytest.mark.unit
def test_ssa_job_succeeded_passes_when_last_run_in_progress(expander):
    db = FakeSsaDbFactory(["RunStatusDescription"], [["In Progress"]])
    config = TestConfig(
        "ssa_job_succeeded",
        "aws_db.MyJobName.succeeded",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    result = SsaJobSucceededTest(config, expander, db).run()

    assert result.status == ResultStatus.OK


@pytest.mark.unit
def test_ssa_job_succeeded_alerts_on_other_status(expander):
    db = FakeSsaDbFactory(["RunStatusDescription"], [["Failed"]])
    config = TestConfig(
        "ssa_job_succeeded",
        "aws_db.MyJobName.succeeded",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    result = SsaJobSucceededTest(config, expander, db).run()

    assert result.status == ResultStatus.ALERT
    assert result.alerts[0].value == "Failed"
    assert "MyJobName" in result.alerts[0].message


@pytest.mark.unit
def test_ssa_job_succeeded_alerts_when_no_run_found(expander):
    db = FakeSsaDbFactory(["RunStatusDescription"], [])
    config = TestConfig(
        "ssa_job_succeeded",
        "aws_db.MyJobName.succeeded",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    result = SsaJobSucceededTest(config, expander, db).run()

    assert result.status == ResultStatus.ALERT
    assert "No run found" in result.alerts[0].message


@pytest.mark.unit
def test_ssa_job_succeeded_defaults_timeframe_to_one_day(expander):
    db = FakeSsaDbFactory(["RunStatusDescription"], [["Succeeded"]])
    config = TestConfig(
        "ssa_job_succeeded",
        "aws_db.MyJobName.succeeded",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    SsaJobSucceededTest(config, expander, db).run()

    _stmt, params = db.connection.executed
    assert params == {
        "job_name": "MyJobName",
        "cutoff_date": 20260811,
        "cutoff_time": 103000,
    }


@pytest.mark.unit
def test_ssa_job_succeeded_applies_a_custom_timeframe(expander):
    db = FakeSsaDbFactory(["RunStatusDescription"], [["Succeeded"]])
    config = TestConfig(
        "ssa_job_succeeded",
        "aws_db.MyJobName.succeeded",
        {
            "connection": "db_aws",
            "job_name": "MyJobName",
            "timeframe": "2 hours",
        },
    )

    SsaJobSucceededTest(config, expander, db).run()

    _stmt, params = db.connection.executed
    assert params == {
        "job_name": "MyJobName",
        "cutoff_date": 20260812,
        "cutoff_time": 83000,
    }


@pytest.mark.unit
def test_ssa_job_errors_reports_failed_runs(expander):
    db = FakeSsaDbFactory(
        ["JobName", "RunDate", "RunTime", "RunStatus", "RunMessage"],
        [["MyJobName", 20260812, 90000, "Failed", "step 2 failed"]],
    )
    config = TestConfig(
        "ssa_job_errors",
        "aws_db.MyJobName.errors",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    result = SsaJobErrorsTest(config, expander, db).run()

    assert result.value == 1
    report = result.reports[0]
    assert report.columns == [
        "JobName",
        "RunDate",
        "RunTime",
        "RunStatus",
        "RunMessage",
    ]
    assert report.rows == [["MyJobName", 20260812, 90000, "Failed", "step 2 failed"]]


@pytest.mark.unit
def test_ssa_job_errors_suppresses_an_empty_report_by_default(expander):
    db = FakeSsaDbFactory(
        ["JobName", "RunDate", "RunTime", "RunStatus", "RunMessage"], []
    )
    config = TestConfig(
        "ssa_job_errors",
        "aws_db.MyJobName.errors",
        {"connection": "db_aws", "job_name": "MyJobName"},
    )

    result = SsaJobErrorsTest(config, expander, db).run()

    assert result.reports == []
    assert result.value == 0


@pytest.mark.unit
def test_ssa_job_errors_notify_if_empty_sends_an_empty_report(expander):
    db = FakeSsaDbFactory(
        ["JobName", "RunDate", "RunTime", "RunStatus", "RunMessage"], []
    )
    config = TestConfig(
        "ssa_job_errors",
        "aws_db.MyJobName.errors",
        {
            "connection": "db_aws",
            "job_name": "MyJobName",
            "notify_if_empty": True,
        },
    )

    result = SsaJobErrorsTest(config, expander, db).run()

    report = result.reports[0]
    assert report.rows == []


# -- opensearch ----------------------------------------------------------


def _search_response(sources):
    return {"hits": {"hits": [{"_source": source} for source in sources]}}


@pytest.mark.unit
def test_opensearch_flag_alerts_on_a_positive_value(expander, extractor):
    factory = FakeOpenSearchFactory(_search_response([{"properties": {"count": 4}}]))
    config = TestConfig(
        "opensearch_flag",
        "es.flag",
        {"connection": "es_prd", "jq": ".properties.count", "query": "{}"},
    )

    result = OpenSearchFlagTest(config, expander, factory, extractor).run()

    assert result.status == ResultStatus.ALERT
    assert result.alerts[0].value == 4


@pytest.mark.unit
def test_opensearch_flag_passes_when_the_value_is_absent(expander, extractor):
    factory = FakeOpenSearchFactory(_search_response([{"properties": {}}]))
    config = TestConfig(
        "opensearch_flag",
        "es.flag",
        {"connection": "es_prd", "jq": ".properties.count", "query": "{}"},
    )

    result = OpenSearchFlagTest(config, expander, factory, extractor).run()

    assert result.status == ResultStatus.OK
    assert result.alerts == []


@pytest.mark.unit
def test_opensearch_flag_expands_the_query(expander, extractor):
    factory = FakeOpenSearchFactory(_search_response([]))
    config = TestConfig(
        "opensearch_flag",
        "es.flag",
        {
            "connection": "es_prd",
            "jq": ".properties.count",
            "query": '{"gte": "{{>today_iso_format<}}"}',
        },
    )

    OpenSearchFlagTest(config, expander, factory, extractor).run()

    assert factory.service.queries[0][0] == '{"gte": "2026-08-12T00:00:00"}'


@pytest.mark.unit
def test_opensearch_report_builds_one_row_per_hit(expander, extractor):
    factory = FakeOpenSearchFactory(
        _search_response(
            [
                {"application": {"name": "app1"}, "properties": {"count": 1}},
                {"application": {"name": "app2"}, "properties": {"count": 2}},
            ]
        )
    )
    config = TestConfig(
        "elasticsearch_report",
        "es.report",
        {
            "connection": "es_prd",
            "jqs": [".application.name", ".properties.count"],
            "query": "{}",
        },
    )

    result = OpenSearchReportTest(config, expander, factory, extractor).run()

    assert result.reports[0].rows == [["app1", 1], ["app2", 2]]


@pytest.mark.unit
def test_opensearch_report_suppresses_an_empty_report_by_default(expander, extractor):
    factory = FakeOpenSearchFactory(_search_response([]))
    config = TestConfig(
        "elasticsearch_report",
        "es.report",
        {
            "connection": "es_prd",
            "jqs": [".application.name"],
            "query": "{}",
        },
    )

    result = OpenSearchReportTest(config, expander, factory, extractor).run()

    assert result.reports == []
    assert result.value == 0
    assert result.message is None


@pytest.mark.unit
def test_opensearch_report_notify_if_empty_sends_an_empty_report(expander, extractor):
    factory = FakeOpenSearchFactory(_search_response([]))
    config = TestConfig(
        "elasticsearch_report",
        "es.report",
        {
            "connection": "es_prd",
            "jqs": [".application.name"],
            "query": "{}",
            "notify_if_empty": True,
        },
    )

    result = OpenSearchReportTest(config, expander, factory, extractor).run()

    assert result.value == 0
    report = result.reports[0]
    assert report.columns == [".application.name"]
    assert report.rows == []


# -- web -----------------------------------------------------------------


@pytest.mark.unit
def test_html_200_passes_on_200(expander, extractor):
    client = FakeHttpClient(status_code=200)
    config = TestConfig("html_200", "web.health", {"url": "https://example.com/health"})

    result = HtmlStatusTest(config, expander, client, extractor).run()

    assert result.status == ResultStatus.OK
    assert client.requested == ["https://example.com/health"]


@pytest.mark.unit
def test_html_200_alerts_on_anything_else(expander, extractor):
    config = TestConfig("html_200", "web.health", {"url": "https://example.com/health"})

    result = HtmlStatusTest(config, expander, FakeHttpClient(503), extractor).run()

    assert result.status == ResultStatus.ALERT
    assert result.alerts[0].value == 503


@pytest.mark.unit
def test_html_verify_ssl_defers_to_client_when_unset(expander, extractor):
    client = FakeHttpClient()
    config = TestConfig("html_200", "web.health", {"url": "https://example.com/"})

    HtmlStatusTest(config, expander, client, extractor).run()

    assert client.verify_ssl is None
    assert client.ca_bundle is None


@pytest.mark.unit
@pytest.mark.parametrize("raw", [False, "false", "no", "0"])
def test_html_verify_ssl_false_disables_verification(expander, extractor, raw):
    client = FakeHttpClient()
    config = TestConfig(
        "html_200", "web.health", {"url": "https://example.com/", "verify_ssl": raw}
    )

    HtmlStatusTest(config, expander, client, extractor).run()

    assert client.verify_ssl is False


@pytest.mark.unit
def test_html_verify_ssl_true_forces_verification(expander, extractor):
    client = FakeHttpClient(body='{"health": {"healthy": 1}}')
    config = TestConfig(
        "html_json_exists",
        "web.health",
        {"url": "https://example.com/", "jq": ".health.healthy", "verify_ssl": True},
    )

    HtmlJsonExistsTest(config, expander, client, extractor).run()

    assert client.verify_ssl is True


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["fasle", "", "{{>unknown<}}"])
def test_html_verify_ssl_invalid_value_is_an_error(expander, extractor, raw):
    client = FakeHttpClient()
    config = TestConfig(
        "html_200", "web.health", {"url": "https://example.com/", "verify_ssl": raw}
    )

    result = HtmlStatusTest(config, expander, client, extractor).run()

    assert result.status == ResultStatus.ERROR
    assert "invalid 'verify_ssl' value" in result.message
    assert client.requested == []


@pytest.mark.unit
def test_html_ca_bundle_is_passed_through(expander, extractor):
    client = FakeHttpClient(body="{}")
    config = TestConfig(
        "html_json_report",
        "web.report",
        {"url": "https://example.com/", "jq": [".a"], "ca_bundle": "/etc/ca.pem"},
    )

    HtmlJsonReportTest(config, expander, client, extractor).run()

    assert client.ca_bundle == "/etc/ca.pem"


@pytest.mark.unit
def test_html_json_exists(expander, extractor):
    config = TestConfig(
        "html_json_exists",
        "web.json",
        {"url": "https://example.com/health", "jq": ".health.healthy"},
    )

    present = HtmlJsonExistsTest(
        config, expander, FakeHttpClient(body='{"health": {"healthy": 1}}'), extractor
    ).run()
    missing = HtmlJsonExistsTest(
        config, expander, FakeHttpClient(body='{"health": {}}'), extractor
    ).run()

    assert present.status == ResultStatus.OK
    assert missing.status == ResultStatus.ALERT


@pytest.mark.unit
def test_html_json_value_compares_loosely(expander, extractor):
    config = TestConfig(
        "html_json_value",
        "web.json",
        {"url": "https://example.com/health", "jq": ".health.healthy", "value": "1"},
    )

    matching = HtmlJsonValueTest(
        config, expander, FakeHttpClient(body='{"health": {"healthy": 1}}'), extractor
    ).run()
    wrong = HtmlJsonValueTest(
        config, expander, FakeHttpClient(body='{"health": {"healthy": 0}}'), extractor
    ).run()

    assert matching.status == ResultStatus.OK
    assert wrong.status == ResultStatus.ALERT
    assert wrong.alerts[0].threshold == "1"


@pytest.mark.unit
def test_html_json_report_builds_a_single_row(expander, extractor):
    config = TestConfig(
        "html_json_report",
        "web.report",
        {
            "url": "https://example.com/health",
            "jq": [".machineName", ".assemblyVersion"],
        },
    )
    client = FakeHttpClient(body='{"machineName": "web01", "assemblyVersion": "1.2.3"}')

    result = HtmlJsonReportTest(config, expander, client, extractor).run()

    assert result.status == ResultStatus.OK
    report = result.reports[0]
    assert report.columns == [".machineName", ".assemblyVersion"]
    assert report.rows == [["web01", "1.2.3"]]
    assert result.value == 1


@pytest.mark.unit
def test_html_json_report_suppresses_an_empty_report_by_default(expander, extractor):
    config = TestConfig(
        "html_json_report",
        "web.report",
        {"url": "https://example.com/health", "jq": [".missing"]},
    )
    client = FakeHttpClient(body="{}")

    result = HtmlJsonReportTest(config, expander, client, extractor).run()

    assert result.reports == []
    assert result.value == 0
    assert result.message is None


@pytest.mark.unit
def test_html_json_report_notify_if_empty_sends_an_empty_report(expander, extractor):
    config = TestConfig(
        "html_json_report",
        "web.report",
        {
            "url": "https://example.com/health",
            "jq": [".missing"],
            "notify_if_empty": True,
        },
    )
    client = FakeHttpClient(body="{}")

    result = HtmlJsonReportTest(config, expander, client, extractor).run()

    assert result.value == 0
    report = result.reports[0]
    assert report.columns == [".missing"]
    assert report.rows == []


@pytest.mark.unit
def test_html_json_report_alerts_on_non_200(expander, extractor):
    config = TestConfig(
        "html_json_report",
        "web.report",
        {"url": "https://example.com/health", "jq": [".machineName"]},
    )

    result = HtmlJsonReportTest(config, expander, FakeHttpClient(503), extractor).run()

    assert result.status == ResultStatus.ALERT
    assert result.alerts[0].value == 503
    assert result.reports == []


@pytest.mark.unit
def test_html_json_report_requires_jq(expander, extractor):
    config = TestConfig(
        "html_json_report", "web.report", {"url": "https://example.com/health"}
    )

    result = HtmlJsonReportTest(config, expander, FakeHttpClient(), extractor).run()

    assert result.status == ResultStatus.ERROR
    assert "jq" in result.error


# -- custom --------------------------------------------------------------


@pytest.fixture()
def custom_loader(tmp_path):
    (tmp_path / "magic_py.py").write_text(
        "def alerting(*args):\n"
        "    return {'name': 'thing', 'message': 'went wrong', 'value': args[0]}\n"
        "\n"
        "def reporting(*args):\n"
        "    return [{'Name': 'a', 'Value': 1}, {'Name': 'b', 'Value': 2}]\n"
        "\n"
        "def quiet(*args):\n"
        "    return None\n"
        "\n"
        "def broken(*args):\n"
        "    raise RuntimeError('boom')\n"
    )

    return CustomTestLoader(str(tmp_path))


@pytest.mark.unit
def test_custom_dict_becomes_an_alert(expander, custom_loader):
    config = TestConfig(
        "custom.magic_py",
        "custom.key",
        {"command": "alerting", "args": ["{{>today<}}"]},
    )

    result = CustomTest(config, expander, custom_loader, "magic_py").run()

    assert result.status == ResultStatus.ALERT
    assert result.alerts[0].value == "2026-08-12"


@pytest.mark.unit
def test_custom_list_becomes_a_report(expander, custom_loader):
    config = TestConfig("custom.magic_py", "custom.key", {"command": "reporting"})

    result = CustomTest(config, expander, custom_loader, "magic_py").run()

    assert result.reports[0].columns == ["Name", "Value"]
    assert result.reports[0].rows == [["a", 1], ["b", 2]]


@pytest.mark.unit
def test_custom_none_passes(expander, custom_loader):
    config = TestConfig("custom.magic_py", "custom.key", {"command": "quiet"})

    result = CustomTest(config, expander, custom_loader, "magic_py").run()

    assert result.status == ResultStatus.OK


@pytest.mark.unit
def test_custom_exceptions_become_errors(expander, custom_loader):
    config = TestConfig("custom.magic_py", "custom.key", {"command": "broken"})

    result = CustomTest(config, expander, custom_loader, "magic_py").run()

    assert result.status == ResultStatus.ERROR
    assert "boom" in result.error


@pytest.mark.unit
def test_custom_missing_command_is_an_error(expander, custom_loader):
    config = TestConfig("custom.magic_py", "custom.key", {"command": "nope"})

    result = CustomTest(config, expander, custom_loader, "magic_py").run()

    assert result.status == ResultStatus.ERROR
