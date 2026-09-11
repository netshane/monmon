"""OpenSearch / elasticsearch backed monitor tests."""

from __future__ import annotations

from ..json_extractor import JsonExtractor
from ..helpers import to_number
from ..monitor_models import TestConfig
from ..monitor_results import Alert, Report, ResultStatus, TestResult
from ..opensearch_service import OpenSearchServiceFactory
from ..value_expander import ValueExpander
from .monitor_test import MonitorTest


class OpenSearchTestBase(MonitorTest):
    def __init__(
        self,
        config: TestConfig,
        value_expander: ValueExpander,
        opensearch_factory: OpenSearchServiceFactory,
        json_extractor: JsonExtractor,
    ):
        super().__init__(config, value_expander)
        self.opensearch_factory = opensearch_factory
        self.json_extractor = json_extractor

    def run_query(self):
        connection = self.required_option("connection")
        query = self.required_option("query")
        index = self.option("index")

        service = self.opensearch_factory.create(str(connection))

        return service.query(query=str(query), index=str(index) if index else None)

    @staticmethod
    def hit_sources(response) -> list:
        """Return the `_source` document of every hit in a search response."""
        hits = ((response or {}).get("hits") or {}).get("hits") or []

        return [hit.get("_source", hit) for hit in hits]


class OpenSearchFlagTest(OpenSearchTestBase):
    """Alerts when the value at `jq` exists and is greater than zero."""

    test_type = "opensearch_flag"

    def execute(self, result: TestResult):
        path = self.required_option("jq")
        response = self.run_query()

        # look in the hit documents first, then fall back to the raw response
        # so aggregation paths such as `.aggregations.total.value` also work
        candidates = self.hit_sources(response)
        value = None
        for candidate in candidates:
            value = self.json_extractor.extract_first(candidate, str(path))
            if value is not None:
                break

        if value is None:
            value = self.json_extractor.extract_first(response, str(path))

        result.value = value
        if value is None:
            result.message = f"No value found at '{path}'"
            return

        numeric = to_number(value)
        if numeric is None:
            result.status = ResultStatus.ERROR
            result.error = f"Value at '{path}' is not numeric (got {value!r})"
            result.message = result.error
            return

        if numeric > 0:
            self.add_alert(
                result,
                Alert(
                    name=self.key,
                    message=f"{path} returned {numeric}",
                    value=numeric,
                ),
            )
            return

        result.message = f"{path} returned {numeric}"


class OpenSearchExistsTest(OpenSearchTestBase):
    """Alerts when the value at `jq` does not exist or is zero."""

    test_type = "opensearch_exists"

    def execute(self, result: TestResult):
        path = self.required_option("jq")
        response = self.run_query()

        # look in the hit documents first, then fall back to the raw response
        # so aggregation paths such as `.aggregations.total.value` also work
        candidates = self.hit_sources(response)
        value = None
        for candidate in candidates:
            value = self.json_extractor.extract_first(candidate, str(path))
            if value is not None:
                break

        if value is None:
            value = self.json_extractor.extract_first(response, str(path))

        result.value = value
        if value is None:
            self.add_alert(
                result,
                Alert(
                    name=self.key,
                    message=f"No value found at {path}",
                    value=None,
                ),
            )
            return

        numeric = to_number(value)
        if numeric is None:
            result.status = ResultStatus.ERROR
            result.error = f"Value at '{path}' is not numeric (got {value!r})"
            result.message = result.error
            return

        if numeric == 0:
            self.add_alert(
                result,
                Alert(
                    name=self.key,
                    message=f"{path} returned {numeric}",
                    value=numeric,
                ),
            )
            return

        result.message = f"{path} returned {numeric}"


class OpenSearchReportTest(OpenSearchTestBase):
    """Builds a table report from the `jqs` paths of every returned hit."""

    test_type = "opensearch_report"

    def execute(self, result: TestResult):
        paths = self.option("jqs") or []
        maxlength_option = self.option("maxlength")
        maxlength = int(maxlength_option) if maxlength_option is not None else 1000
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise ValueError(
                f"Test '{self.key}' ({self.config.test_type}) requires a 'jqs' setting"
            )

        response = self.run_query()
        documents = self.hit_sources(response)

        rows = []
        for document in documents:
            values = []
            for p in paths:
                value = self.json_extractor.extract_first(document, str(p))
                if isinstance(value, str):
                    value = value[:maxlength]
                values.append(value)
            rows.append(values)

        notify_if_empty = self.option("notify_if_empty", False)
        if rows or notify_if_empty:
            self.add_report(
                result,
                Report(title=self.key, columns=[str(p) for p in paths], rows=rows),
            )
        result.value = len(rows)
