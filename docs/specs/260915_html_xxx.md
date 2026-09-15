# html xxx

Create a new test "test.html_xxx" that checks for a specific status code from an HTTP request.  If the response does not match the expected status code, an alert is generated.  If no response is received, an alert is also generated.

This is intended to be a new test. `test.html_200` is a separate test that is simplified since it will be the most commonly used.

No response should be treated as an alert and not an error.

Add unit tests and update documentation.

```toml
[test.html_xxx]
key = "network.appgroup.app.html_xxx"  # a unique identifier for the monitor used to store results
expected_status = 401  # the expected HTTP status code
url = "https://example.com"  # the URL to check
```

## Proposed plan

1. **`locallib/monitor_tests/web_tests.py`** - add `HtmlXxxTest(WebTestBase)`:
   - `test_type = "html_xxx"`
   - reads `expected_status` via `self.required_option("expected_status")` (no
     default - unlike `html_200`, this type has no sensible implicit default)
   - calls `self.fetch()` wrapped in a `try/except` around the request-failure
     exception(s) raised by `http_client.get`, and on failure calls
     `self.add_alert()` with a "no response received" message instead of
     letting it propagate to the base class's `ResultStatus.ERROR` handling
   - on response received, compares `response.status_code` to
     `expected_status`, alerts on mismatch (mirroring `HtmlStatusTest`'s
     pattern), sets `result.message` on match
2. **`locallib/monitor_tests/__init__.py`** - export `HtmlXxxTest`.
3. **`locallib/monitor_test_factory.py`** - register `"html_xxx"` in
   `_builders()` using the existing `_web` helper (same DI as `html_200`).
4. **`docs/monitors.md`** - add a `[test.html_xxx]` entry near
   `[test.html_200]`, documenting `expected_status` (required), `url`, and the
   no-response-is-an-alert behavior.
5. **`tests/unit/test_monitor_tests.py`** - add cases: status matches expected
   (ok), status mismatches (alert), no response/connection failure (alert, not
   error).
6. `uvx ruff format` && `uvx ruff check --fix`.
7. Per `CLAUDE.md`, append an implementation summary with the session id to
   the end of this spec file when done.

## Implementation summary

Implemented as proposed:

- Added `HtmlXxxTest` to `locallib/monitor_tests/web_tests.py`. It requires
  `expected_status` (no default), fetches the URL, and treats a request
  exception (`requests.exceptions.RequestException`) as an alert rather than
  letting it propagate to the base class's `ResultStatus.ERROR` handling. On
  a received response it alerts when `status_code != expected_status`,
  mirroring `HtmlStatusTest`.
- Exported `HtmlXxxTest` from `locallib/monitor_tests/__init__.py`.
- Registered `"html_xxx"` in `MonitorTestFactory._builders()` using the
  existing `_web` helper.
- Documented `[test.html_xxx]` in `docs/monitors.md` (behaviour table row and
  a note that `expected_status` is required, with no implicit default).
- Added unit tests in `tests/unit/test_monitor_tests.py` covering a matching
  status (ok), a mismatched status (alert), a failed request (alert, not
  error), and a missing `expected_status` (error). `FakeHttpClient` gained a
  `raises` option to simulate a connection failure.
- Ran `uvx ruff format` and `uvx ruff check --fix`; full unit test suite
  (568 tests) passes.

Claude session id: 6ab6d122-5076-4557-8452-9109154d4870
