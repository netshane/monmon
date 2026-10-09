# HTML test timeout

Extend the `html_` tests to allow for a timeout setting.

- the test should alert if the timeout is exceeded
- modify the `html_200`, `html_xxx`, and `html_json*` tests to support this new settings.

---

## Review (2026-10-09)

Status: reviewed, not implemented.  Plan doc will be written to
`docs/agent/plans/261009_html_timeout_plan.md` once the open questions are answered.

### Findings

- `HttpClient` already has a global `timeout_seconds` (`[default.http]`, default 30),
  passed to `requests`.  Nothing per request exists today.
- Only `html_xxx` catches `requests.exceptions.RequestException`.  `html_200` and the
  `html_json*` tests let it escape, and `MonitorTest` turns that into
  `ResultStatus.ERROR`, not an alert.  "Alert if the timeout is exceeded" therefore needs
  new handling in those tests.
- `html_xxx` catches all `RequestException`s, so a timeout there is already an alert
  but is worded "no response received".

### Decisions added to the spec (low risk)

- Applies to every `WebTestBase` test (`html_200`/`html_status`, `html_xxx`, `html_json_*`),
  as in the html extension spec.
- New optional `timeout_seconds` setting per test (same name as `[default.http]`).
  Unset uses the client default.  Value must be a number > 0 (int or float, jinja expanded);
  otherwise `ValueError` as for `verify_ssl`.
- `HttpClient.request()` and `get()` gain `timeout_seconds: float | None = None`
  (`None` = client default), the same pattern as `verify_ssl`.
- `requests.exceptions.Timeout` (connect or read) is caught in `WebTestBase.fetch`
  and re-raised as a small `HttpTimeout`-style signal handled by one shared helper, so each
  test emits an `Alert` named after the url:
  `<url> timed out after <n>s`, with `threshold=<n>`.  The result is an alert, not an error.
- Other connection failures keep today's behaviour (error for `html_200`/`html_json*`,
  alert for `html_xxx`).
- `post_json` (notifications) is untouched.
- No new test type.  Default behaviour with no setting is unchanged apart from the
  timeout now being an alert.

### Questions for you

1. **Behaviour change:** with no `timeout_seconds` set, a hit on the *global* 30s timeout
   will now also become an alert instead of an error for `html_200`/`html_json*`.  This
   seems right, but it changes existing results.  OK, or alert only when the per-test
   setting is present?
2. **Meaning of "exceeded":** should the setting be a hard cutoff (requests timeout, which
   bounds connect and each read, not total time), or should a slow-but-successful
   response with `elapsed_seconds > timeout` also alert?  `requests` timeouts do not cap
   total download time.  Recommend the requests timeout only for now.
3. Should `html_xxx` keep the generic "no response received" text for non-timeout failures?
   (Recommend yes.)
4. Is a per-test connect/read split (`timeout = [3, 10]`) wanted?  Recommend no.

### Proposed implementation plan

1. `http_client.py`: add `timeout_seconds` param to `request`/`get`; use
   `timeout_seconds if not None else self.timeout_seconds`.
2. `web_tests.py`: add `_timeout()` parser (like `_verify_ssl`); pass it from `fetch()`;
   wrap the request in `fetch()` to convert `Timeout` into a shared alert (one helper used
   by `html_200`, `html_xxx` and `fetch_json`).
3. Tests: `test_http_client.py` (override and default), `test_monitor_tests.py` (each of the
   five tests alerts on timeout; invalid values raise; unset unchanged).
4. Docs: document the setting in `settings.toml` examples and `docs/architecture.md` if it
   lists test options; add implementation summary to this spec.
5. Run `uv run ruff format`, `ruff check --fix`, `mypy`, `pytest`.

### Change log of this review

- Appended this review section; the original request text was not modified.

### Answers (2026-10-09, accepted)

1. Yes - hitting the global timeout also becomes an alert for `html_200` / `html_json*`.
2. "Exceeded" means the `requests` timeout definition only (no total-time or `elapsed_seconds` check).
3. `html_xxx` keeps the generic "no response received" text for non-timeout failures.
4. No per-test connect/read split; a single number only.

Review accepted.  Plan: `docs/agent/plans/261009_html_timeout_plan.md`.

## Implementation summary (2026-10-09)

- `HttpClient.request()` / `get()` take `timeout_seconds: float | None = None` (`None` = client default).
- `WebTestBase._timeout()` parses the per-test `timeout_seconds` (number > 0, jinja expanded; bool, 0, negative, inf/nan or non-numeric -> `ValueError`).
- `WebTestBase.fetch()` converts `requests.exceptions.Timeout` into `FetchTimeout(url, seconds)` (effective timeout);
  `add_timeout_alert()` raises the alert `<url> timed out after <n>s` with `threshold=<n>`.
  Handled in `html_200`, `html_xxx` (before the generic `RequestException`) and `fetch_json` (json tests).
- Tests added in `tests/unit/test_http_client.py` and `tests/unit/test_monitor_tests.py`; documented in `docs/monitors.md`.
- ruff, mypy and pytest (623 tests) pass.
