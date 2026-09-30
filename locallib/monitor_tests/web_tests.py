"""Web page monitor tests."""

from __future__ import annotations

import json

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
        method = self._method()
        body, headers = self._body_and_headers()
        verify_ssl = self._verify_ssl()
        ca_bundle = self.option("ca_bundle")

        self.response = self.http_client.request(
            method,
            url,
            headers=headers,
            body=body,
            verify_ssl=verify_ssl,
            ca_bundle=str(ca_bundle) if ca_bundle else None,
            allow_redirects=self._allow_redirects(),
        )
        return self.response

    def _invalid(self, option: str, value, expected: str) -> ValueError:
        return ValueError(
            f"Test '{self.key}' ({self.config.test_type}) has an invalid "
            f"'{option}' value {value!r} - expected {expected}"
        )

    def _method(self) -> str:
        # passed through as written: methods are case sensitive and unusual
        # ones are a valid test case
        method = self.option("method", "GET")
        if not isinstance(method, str) or not method.strip():
            raise self._invalid("method", method, "an http method")

        return method.strip()

    def _verify_ssl(self) -> bool | None:
        # tri-state on purpose: unset defers to the [http] setting, an explicit
        # true forces verification on for this test even when the global is off
        raw = self.option("verify_ssl")
        verify_ssl = to_bool(raw)
        if raw is not None and verify_ssl is None:
            raise self._invalid("verify_ssl", raw, "true or false")

        return verify_ssl

    def _allow_redirects(self) -> bool:
        raw = self.option("allow_redirects")
        if raw is None:
            return True

        allow = to_bool(raw)
        if allow is None:
            raise self._invalid("allow_redirects", raw, "true or false")

        return allow

    def _body_and_headers(self) -> tuple[str | bytes | None, dict | None]:
        """Build the request body and headers, applying the precedence rules.

        The user's `headers` table is copied, never mutated.  `content_type`
        (or its alias `content-type`) replaces any `Content-Type` header; a table / array body is sent as
        json (with `application/json` unless a content type was given); any
        `Content-Length` header is dropped when a body is sent.
        """
        raw_headers = self.option("headers")
        if raw_headers and not isinstance(raw_headers, dict):
            raise self._invalid("headers", raw_headers, "a table of header names")
        headers = dict(raw_headers) if raw_headers else {}

        raw_body = self.option("body")
        body: str | bytes | None
        if raw_body is None:
            body = None
        elif isinstance(raw_body, (dict, list)):
            try:
                body = json.dumps(raw_body)
            except (TypeError, ValueError) as e:
                raise self._invalid(
                    "body", "<table>", f"json serialisable values ({e})"
                ) from e
            if not self._has_header(headers, "content-type"):
                headers["Content-Type"] = "application/json"
        else:
            # http.client would encode a str as latin-1 and fail on e.g. "€"
            body = str(raw_body).encode("utf-8")

        content_type = self.option("content_type")
        if content_type is None:
            content_type = self.option("content-type")
        if content_type:
            self._drop_header(headers, "content-type")
            headers["Content-Type"] = str(content_type)

        if body is not None:
            self._drop_header(headers, "content-length")

        return body, headers or None

    @staticmethod
    def _has_header(headers: dict, name: str) -> bool:
        return any(str(key).lower() == name for key in headers)

    @staticmethod
    def _drop_header(headers: dict, name: str) -> None:
        for key in [k for k in headers if str(k).lower() == name]:
            del headers[key]

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
