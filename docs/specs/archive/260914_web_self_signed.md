# web self signed

Update the web monitor tests in `locallib/monitor_tests/web_tests.py` to have an option to accept self-signed SSL certificates.
The option should be `verify_ssl` and should default to `true`. When set to `false`, the web monitor tests should not verify SSL certificates.

- ca_bundle should be in scope now
- suppress `InsecureRequestWarning` when `verify_ssl` is set to `false`
- simulate does not need to validate warn when this option is set to `false`
- an explicit `verify_ssl = true` should force verification on for the specific test and override global settings just for that test

```toml
[test.html_200]
key = "app.html_200"
url = "https://example.com"
verify_ssl = false

[test.html_json_exists]
# monitor will load a web page as json and check to see if a json string exists
key = "network.appgroup.app.html"  # a unique identifier for the monitor used to store results
url = "https://example.com/app/health"
jq = ".health.healthy"
verify_ssl = false


[test.html_json_value]
# montior will load a web page, parse it as json, and alert if the given json key does not equal the specified value
key = "network.appgroup.app.html"  # a unique identifier for the monitor used to store results
url = "https://example.com/app/health"
jq = ".health.healthy"
value = "1"
verify_ssl = true # default

[test.html_json_report]
# Query the provided URL
# Generate a report based on the JSON response using the provided jq expressions
# Store the report in structured text in the monitor results for later retrieval and reporting
key = "app.report"
notify_if_empty = false  # default value, set to true to notify even if no rows
url = "https://app.example.com/api/health/check"
jq = [
    ".databaseServer",
    ".machineName",
    ".assemblyVersion",
]
verify_ssl = false

```
## Implementation summary

Session: https://claude.ai/code/session_017zhKmQa8LheniYA4pjhTKB

- `locallib/http_client.py` - `HttpClient.get()` takes `verify_ssl: bool | None`
  (`None` = client default from `[http] verify_ssl`) and `ca_bundle: str | None`.
  A `ca_bundle` is passed to `requests` as the `verify` path when verifying and
  ignored when verification is off. `InsecureRequestWarning` is suppressed
  inside the request only when `verify` resolves to `False`. `post_json()` and
  the notification client (`get_notification_http_client()`) are unchanged, so
  chat credentials are never sent with verification disabled.
- `locallib/monitor_tests/web_tests.py` - `WebTestBase.fetch()` reads the
  tri-state `verify_ssl` option and `ca_bundle` and passes them through, so all
  `html_*` test types pick the options up with no per-class changes. A
  `verify_ssl` value that is present but not a recognisable boolean (a typo,
  an unexpanded template token) raises, so the test reports `ERROR` with the
  bad value rather than silently deferring to the global setting.
- `locallib/helpers.py` - `to_bool()` coerces toml bools and `"true"` /
  `"false"` strings, returning `None` when unset (`bool("false")` is `True`).
- Tests: `tests/unit/test_http_client.py` (new) covers default / override /
  ca_bundle / warning suppression; `tests/unit/test_monitor_tests.py` covers
  the option plumbing through the web tests.
- Docs: `docs/monitors.md` and `monitors/sample.toml` describe both options.
