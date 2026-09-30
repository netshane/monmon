# html extension

Extend the `html_` tests to allow for additional methods, content types, and custom bodies.

- modify the `html_200`, `html_xxx`, and `html_json*` tests to support these new settings.

- add a `method` setting that allows specifying the HTTP method for the test (e.g., `GET`, `POST`, `PUT`, `DELETE`).
  - this is an option setting that should default to `GET` if not specified.
  - the method list should not be restricted and can include any valid HTTP method.  Invalid / unusual methods may be required for test cases and will be supported.    

- add a `content_type` setting that allows specifying the `Content-Type` header for the test (e.g., `text/html`, `application/json`).
  - this is an optional setting 
  - this setting should win if both the `content_type` setting and the `Content-Type` header are specified.

- add a `body` setting that allows specifying the request body for the test.
  - this is an optional setting that should default to an empty body if not specified.
  - jinja2 expansion of the body should be documented
  - this setting should take precedence over any `Content-Length` header if both are specified.
  - a body written as a TOML table should be converted to json and the `Content-Type` header should be set to `application/json` if not already specified.

- add a `headers` setting that allows specifying custom headers for the test.
  - this is an optional setting

- add an `allow_redirects` setting that allows specifying whether redirects should be followed for the test.
  - this is an optional setting that should default to `true` if not specified.

---

## Proposed Plan

Status: proposed, accepted after review on 2026-09-30.  Not yet implemented.

### Behaviour decisions (from review)

- Applies to every test built on `WebTestBase`: `html_200` (and its `html_status`
  alias), `html_xxx`, `html_json_exists`, `html_json_value`, `html_json_report`.
- `method` - passed through as written, only surrounding whitespace is stripped.
  It is not uppercased (methods are case sensitive, and odd methods are a valid
  test case).  Empty / whitespace only is an error.  Anything `requests` /
  `http.client` refuses (e.g. control characters) surfaces as a normal request
  failure.  Default `GET`.
- `content_type` - no default.  When set, any `Content-Type` key in `headers`
  (matched case insensitively) is removed and replaced by `content_type`.
- `body` - default none (no body sent).
  - a string is sent as is, after jinja expansion; no content type is implied.
  - a TOML table (or array) is serialised with `json.dumps` after jinja
    expansion of its values, and `Content-Type: application/json` is set unless
    `content_type` or a `Content-Type` header was given.
  - when a body is present any `Content-Length` header (case insensitive) is
    dropped so `requests` computes the real length.  With no body a
    user supplied `Content-Length` is left untouched.
  - the body is never written into alert messages, logs, or `result_json`.
- `headers` - existing option, unchanged apart from the two precedence rules
  above.  The user's dict is copied, never mutated.
- `allow_redirects` - default `true`, parsed with `to_bool` and rejected with a
  `ValueError` on an unrecognised value (same pattern as `verify_ssl`).
  Documented caveat: `requests` turns a POST into a GET and drops the body when
  following a 301/302/303.
- Without any of the new options set, the outgoing request is identical to
  today's (GET, no Content-Type, redirects followed).

### Implementation steps

1. `locallib/http_client.py`
   - add `HttpClient.request(method, url, headers=None, body=None,
     verify_ssl=None, ca_bundle=None, allow_redirects=True) -> HttpResponse`
     using `requests.request(...)` with the existing `_verify()` and
     `InsecureRequestWarning` handling.  `body` is `str | bytes | None`.
   - reimplement `get()` as a thin wrapper over `request("GET", ...)` so its
     signature and callers are unchanged.  `post_json()` (Slack/Teams) is left
     alone.
2. `locallib/monitor_tests/web_tests.py`
   - split `WebTestBase.fetch()` into small helpers: `_method()`,
     `_verify_ssl()` (moved from the current inline code), `_allow_redirects()`,
     and `_body_and_headers()` which applies the json serialisation and the
     Content-Type / Content-Length precedence rules.
   - `fetch()` calls `self.http_client.request(...)`; subclasses are unchanged.
   - error messages follow the existing
     `Test '<key>' (<type>) has an invalid '<option>' value ...` form.
   - `html_xxx` keeps catching `RequestException` as an alert, so an unsendable
     method there is an alert; elsewhere it is an error via the base class.
3. Tests
   - `tests/unit/test_monitor_tests.py`: extend `FakeHttpClient` with
     `request()` recording method, headers, body and allow_redirects (keep
     `get()` delegating to it).  Cases:
     - defaults: GET, no body, no Content-Type, redirects on (regression)
     - custom and lowercase methods passed through untouched; empty method errors
     - string body sent as is; jinja expression in body expanded
     - table body -> json string + `application/json`
     - table body with explicit `content_type` keeps the explicit value
     - `content_type` overrides `headers."content-type"` (case insensitive)
     - `Content-Length` header dropped when a body is set, kept when not
     - `allow_redirects = false` passed through; invalid value errors
     - `html_json_value` over POST; `html_xxx` expecting 302 with redirects off
     - user `headers` dict not mutated
   - `tests/unit/test_http_client.py`: `request()` forwards method, data,
     headers, allow_redirects and verify to a monkeypatched `requests.request`;
     `get()` still behaves as before.
4. Docs
   - `docs/monitors.md`: add `method`, `content_type`, `body`,
     `allow_redirects` to the `html_*` option list with the precedence rules,
     the table -> json conversion, jinja expansion of the body (including the
     literal `{{` caveat for json strings) and the redirect caveat.
   - `monitors/sample.toml`: add a POST example with a table body and one
     `html_xxx` redirect check with `allow_redirects = false`.
   - `docs/architecture.md`: note `HttpClient.request()` as the entry point for
     the web tests in the supporting modules table.
5. Finish
   - `uv run ruff format`, `uv run ruff check --fix`, `uv run mypy`,
     `uv run pytest -m unit`.
   - append the implementation summary (with session id) to this spec.

### Files touched

`locallib/http_client.py`, `locallib/monitor_tests/web_tests.py`,
`tests/unit/test_monitor_tests.py`, `tests/unit/test_http_client.py`,
`docs/monitors.md`, `docs/architecture.md`, `monitors/sample.toml`.
No changes to the factory, runner, service, models or cli.

---

## Implementation Summary

Session: 6bcdfba4-e88a-4f84-b341-ac0edeaa35da (2026-09-30)

- `HttpClient.request()` added (method, body, headers, verify, ca_bundle,
  allow_redirects); `get()` is now a thin wrapper over it, `post_json()` untouched.
- `WebTestBase.fetch()` split into `_method()`, `_verify_ssl()`,
  `_allow_redirects()`, `_body_and_headers()`; applies the Content-Type /
  Content-Length precedence rules and table -> json conversion without mutating
  the user's `headers`.
- Tests added in `test_monitor_tests.py` and `test_http_client.py`; the existing
  http client fixture now patches `requests.request`.
- Docs updated: `docs/monitors.md`, `docs/architecture.md`, `monitors/sample.toml`.
- ruff, mypy and `pytest -m unit` (596 tests) all pass.
