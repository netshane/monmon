"""Web page monitor tests."""

from __future__ import annotations

import requests

from ..helpers import to_bool
from ..http_client import HttpClient
from ..json_extractor import JsonExtractor
from ..monitor_models import TestConfig
from ..monitor_results import Alert, Report, TestResult
from ..value_expander import ValueExpander
from .monitor_test import MonitorTest


class WebTestBase(MonitorTest):
    def __init__(
        self,
        config: TestConfig,
        value_expander: ValueExpander,
        http_client: HttpClient,
        json_extractor: JsonExtractor,
    ):
        super().__init__(config, value_expander)
        self.http_client = http_client
        self.json_extractor = json_extractor

    def fetch(self):
        url = str(self.required_option("url"))
        headers = self.option("headers")

        # tri-state on purpose: unset defers to the [http] setting, an explicit
        # true forces verification on for this test even when the global is off
        raw_verify_ssl = self.option("verify_ssl")
        verify_ssl = to_bool(raw_verify_ssl)
        if raw_verify_ssl is not None and verify_ssl is None:
            raise ValueError(
                f"Test '{self.key}' ({self.config.test_type}) has an invalid "
                f"'verify_ssl' value {raw_verify_ssl!r} - expected true or false"
            )
        ca_bundle = self.option("ca_bundle")

        self.response = self.http_client.get(
            url,
            headers=headers if headers else None,
            verify_ssl=verify_ssl,
            ca_bundle=str(ca_bundle) if ca_bundle else None,
        )
        return self.response

    def fetch_json(self, result: TestResult):
        """Fetch the response and parse it as JSON.

        Alerts and returns `None` when the response status is not 200, so
        callers only need to handle the success path. `self.response` is set
        either way, for callers that need the url in their own messages.
        """
        response = self.fetch()

        if response.status_code != 200:
            self.add_alert(
                result,
                Alert(
                    name=response.url,
                    message=f"{response.url} returned {response.status_code}",
                    value=response.status_code,
                ),
            )
            return None

        return response.json()


class HtmlStatusTest(WebTestBase):
    """Alerts when a page returns anything other than the expected status."""

    test_type = "html_200"

    def execute(self, result: TestResult):
        expected = int(self.option("status_code", 200) or 200)
        response = self.fetch()
        result.value = response.status_code

        if response.status_code != expected:
            self.add_alert(
                result,
                Alert(
                    name=response.url,
                    message=f"{response.url} returned {response.status_code} "
                    f"(expected {expected})",
                    value=response.status_code,
                    threshold=expected,
                ),
            )
            return

        result.message = f"{response.url} returned {response.status_code}"


class HtmlXxxTest(WebTestBase):
    """Alerts when a page returns anything other than an explicit expected status.

    Unlike `html_200`, `expected_status` has no default - this test exists for
    the less common status codes (e.g. an endpoint that is expected to
    require auth and return 401). A failed request (no response received) is
    also an alert rather than an error.
    """

    test_type = "html_xxx"

    def execute(self, result: TestResult):
        expected = int(self.required_option("expected_status"))

        try:
            response = self.fetch()
        except requests.exceptions.RequestException as e:
            self.add_alert(
                result,
                Alert(
                    name=self.key,
                    message=f"no response received from {self.option('url')}: {e}",
                ),
            )
            return

        result.value = response.status_code

        if response.status_code != expected:
            self.add_alert(
                result,
                Alert(
                    name=response.url,
                    message=f"{response.url} returned {response.status_code} "
                    f"(expected {expected})",
                    value=response.status_code,
                    threshold=expected,
                ),
            )
            return

        result.message = f"{response.url} returned {response.status_code}"


class HtmlJsonExistsTest(WebTestBase):
    """Alerts when the given json path is missing from the response."""

    test_type = "html_json_exists"

    def execute(self, result: TestResult):
        path = str(self.required_option("jq"))
        document = self.fetch_json(result)
        if document is None:
            return

        value = self.json_extractor.extract_first(document, path)
        result.value = value

        if value is None:
            self.add_alert(
                result,
                Alert(
                    name=self.response.url,
                    message=f"{self.response.url} response has no value at '{path}'",
                ),
            )
            return

        result.message = f"'{path}' found with value {value}"


class HtmlJsonValueTest(WebTestBase):
    """Alerts when the json path does not equal the expected value."""

    test_type = "html_json_value"

    def execute(self, result: TestResult):
        path = str(self.required_option("jq"))
        expected = self.required_option("value")
        document = self.fetch_json(result)
        if document is None:
            return

        value = self.json_extractor.extract_first(document, path)
        result.value = value

        if not self._matches(value, expected):
            self.add_alert(
                result,
                Alert(
                    name=self.response.url,
                    message=f"{self.response.url} '{path}' is {value!r}, expected {expected!r}",
                    value=value,
                    threshold=expected,
                ),
            )
            return

        result.message = f"'{path}' matched {expected!r}"

    @staticmethod
    def _matches(value, expected) -> bool:
        if value == expected:
            return True

        # toml values are commonly strings ("1"), json values commonly are not
        return str(value).strip().lower() == str(expected).strip().lower()


class HtmlJsonReportTest(WebTestBase):
    """Builds a single row report from the `jq` paths of the response."""

    test_type = "html_json_report"

    def execute(self, result: TestResult):
        paths = self.option("jq") or []
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise ValueError(
                f"Test '{self.key}' ({self.config.test_type}) requires a 'jq' setting"
            )

        document = self.fetch_json(result)
        if document is None:
            return

        row = [self.json_extractor.extract_first(document, str(p)) for p in paths]
        rows = [row] if any(value is not None for value in row) else []

        notify_if_empty = self.option("notify_if_empty", False)
        if rows or notify_if_empty:
            self.add_report(
                result,
                Report(title=self.key, columns=[str(p) for p in paths], rows=rows),
            )
        result.value = len(rows)
