# Plan: html test timeout

Source spec: `docs/agent/specs/261009_html_timeout.md` (review accepted 2026-10-09).
Status: implemented 2026-10-09.

## Decisions
- Per-test optional `timeout_seconds` on every `WebTestBase` test (`html_200`/`html_status`,
  `html_xxx`, `html_json_exists`, `html_json_value`, `html_json_report`).
- Number > 0 (int or float, jinja expanded); invalid -> `ValueError` (same style as `verify_ssl`).
- Unset -> client default (`[default.http] timeout_seconds`).
- "Exceeded" = `requests.exceptions.Timeout` only.  No `elapsed_seconds` check, no connect/read split.
- Any `Timeout` (per-test or global) is an alert, not an error:
  `<url> timed out after <n>s`, `threshold=<n>`, alert name = url.
- `html_xxx` keeps "no response received ..." for other `RequestException`s;
  `html_200` / `html_json*` keep erroring on non-timeout failures.
- `post_json` / notification client untouched.

## Steps
1. `locallib/http_client.py`: add `timeout_seconds: float | None = None` to `get()` and
   `request()`; use it when not `None`, else `self.timeout_seconds`.
2. `locallib/monitor_tests/web_tests.py`:
   - add `_timeout()` parser next to `_verify_ssl()`.
   - `fetch()` passes it through and records the effective timeout (override or
     `http_client.timeout_seconds`).
   - catch `requests.exceptions.Timeout` in `fetch()` and raise a small internal
     `FetchTimeout(url, seconds)` exception; add a shared helper that turns it into the alert.
   - `html_200`: handle `FetchTimeout`.  `fetch_json`: handle it and return `None`.
   - `html_xxx`: catch `FetchTimeout` before `RequestException`.
3. Tests (pytest, `unit` marker):
   - `tests/unit/test_http_client.py`: override used; `None` uses client default.
   - `tests/unit/test_monitor_tests.py`: each of the five tests alerts on timeout with the
     expected message/threshold; global-default timeout alerts too; invalid values
     (0, negative, "abc", bool) raise; unset behaviour unchanged; `html_xxx` non-timeout
     failure text unchanged.
4. Docs: document `timeout_seconds` in the `settings.toml` examples and
   `docs/architecture.md` if it lists html options.
5. Run `uv run ruff format`, `uv run ruff check --fix`, `uv run mypy`, `uv run pytest`.
6. On completion write an implementation summary into the spec and this plan; add any
   lessons to `docs/agent/memory/MEMORY.md`.

## Implementation summary (2026-10-09)

- `HttpClient.request()` / `get()` take `timeout_seconds: float | None = None` (`None` = client default).
- `WebTestBase._timeout()` parses the per-test `timeout_seconds` (number > 0, jinja expanded; bool, 0, negative, inf/nan or non-numeric -> `ValueError`).
- `WebTestBase.fetch()` converts `requests.exceptions.Timeout` into `FetchTimeout(url, seconds)` (effective timeout);
  `add_timeout_alert()` raises the alert `<url> timed out after <n>s` with `threshold=<n>`.
  Handled in `html_200`, `html_xxx` (before the generic `RequestException`) and `fetch_json` (json tests).
- Tests added in `tests/unit/test_http_client.py` and `tests/unit/test_monitor_tests.py`; documented in `docs/monitors.md`.
- ruff, mypy and pytest (623 tests) pass.
