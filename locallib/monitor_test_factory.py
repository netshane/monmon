"""Builds runnable test objects from `[test.*]` configuration sections."""

from __future__ import annotations

from .custom_test_loader import CustomTestLoader
from .db_connection_factory import DbConnectionFactory
from .docker_service import DockerServiceFactory
from .http_client import HttpClient
from .json_extractor import JsonExtractor
from .monitor_models import TestConfig
from .monitor_tests import (
    CustomTest,
    DbExistsTest,
    DbFlagTest,
    DbNoRowsTest,
    DbReportTest,
    DbThresholdTest,
    DockerContainerRunningTest,
    HtmlJsonExistsTest,
    HtmlJsonReportTest,
    HtmlJsonValueTest,
    HtmlStatusTest,
    HtmlXxxTest,
    MonitorTest,
    OpenSearchFlagTest,
    OpenSearchExistsTest,
    OpenSearchReportTest,
    PingTest,
    SsaJobErrorsTest,
    SsaJobSucceededTest,
)
from .opensearch_service import OpenSearchServiceFactory
from .ping_client import PingClient
from .value_expander import ValueExpander


class MonitorTestFactory:
    """Maps a test type name onto a test class and injects its dependencies."""

    def __init__(
        self,
        value_expander: ValueExpander,
        json_extractor: JsonExtractor,
        db_factory: DbConnectionFactory,
        docker_factory: DockerServiceFactory,
        opensearch_factory: OpenSearchServiceFactory,
        http_client: HttpClient,
        ping_client: PingClient,
        custom_test_loader: CustomTestLoader,
    ):
        self.value_expander = value_expander
        self.json_extractor = json_extractor
        self.db_factory = db_factory
        self.docker_factory = docker_factory
        self.opensearch_factory = opensearch_factory
        self.http_client = http_client
        self.ping_client = ping_client
        self.custom_test_loader = custom_test_loader

    def known_test_types(self) -> list[str]:
        return sorted(self._builders().keys()) + ["custom.<module>"]

    def create(self, config: TestConfig) -> MonitorTest:
        test_type = config.test_type.lower()

        if test_type.startswith("custom."):
            module_name = config.test_type.split(".", 1)[1]
            return CustomTest(
                config=config,
                value_expander=self.value_expander,
                custom_test_loader=self.custom_test_loader,
                module_name=module_name,
            )

        builder = self._builders().get(test_type)
        if builder is None:
            raise ValueError(
                f"Unknown test type '{config.full_name}' for test key '{config.key}'. "
                f"Known types: {', '.join(self.known_test_types())}"
            )

        return builder(config)

    def _builders(self) -> dict:
        return {
            "ping": self._db_free(PingTest, ping_client=self.ping_client),
            "dbflag": self._db(DbFlagTest),
            "dbthreshold": self._db(DbThresholdTest),
            "dbreport": self._db(DbReportTest),
            "dbnorows": self._db(DbNoRowsTest),
            "dbexists": self._db(DbExistsTest),
            "docker_container_running": self._docker(DockerContainerRunningTest),
            "ssa_job_succeeded": self._db(SsaJobSucceededTest),
            "ssa_job_errors": self._db(SsaJobErrorsTest),
            "opensearch_flag": self._search(OpenSearchFlagTest),
            "opensearch_exists": self._search(OpenSearchExistsTest),
            "elasticsearch_flag": self._search(OpenSearchFlagTest),
            "opensearch_report": self._search(OpenSearchReportTest),
            "elasticsearch_report": self._search(OpenSearchReportTest),
            "html_200": self._web(HtmlStatusTest),
            "html_status": self._web(HtmlStatusTest),
            "html_xxx": self._web(HtmlXxxTest),
            "html_json_exists": self._web(HtmlJsonExistsTest),
            "html_json_value": self._web(HtmlJsonValueTest),
            "html_json_report": self._web(HtmlJsonReportTest),
        }

    def _db_free(self, cls, **kwargs):
        def build(config: TestConfig) -> MonitorTest:
            return cls(config=config, value_expander=self.value_expander, **kwargs)

        return build

    def _db(self, cls):
        return self._db_free(cls, db_factory=self.db_factory)

    def _docker(self, cls):
        return self._db_free(cls, docker_factory=self.docker_factory)

    def _search(self, cls):
        return self._db_free(
            cls,
            opensearch_factory=self.opensearch_factory,
            json_extractor=self.json_extractor,
        )

    def _web(self, cls):
        return self._db_free(
            cls,
            http_client=self.http_client,
            json_extractor=self.json_extractor,
        )
