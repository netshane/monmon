# report notify if empty

For report tests, add a new option `notify_if_empty` which defaults to `false`. If set to `true`, the report will be notified even if it is empty. This is useful for reports that are expected to be empty at times, but you still want to receive them.

an "empty" report is defined as a report that has no rows.

html_json_report should be updated so that it returns an empty list of rows when no values were extracted.

This should be added to the following report types:
- `test.opensearch_report`
- `test.html_json_report`
- `test.dbreport`


- `test.custom` is intentionally excluded.  The logic in the test can make the determination.

- a `result.message` should not be set if the report is suppressed.

## Implementation summary

- `DbReportTest`, `OpenSearchReportTest`, and `HtmlJsonReportTest`
  (`locallib/monitor_tests/db_tests.py`, `opensearch_tests.py`, `web_tests.py`)
  now read a `notify_if_empty` option (default `false`) and only call
  `add_report()` when the report has rows or `notify_if_empty` is `true`.
  Skipping `add_report()` leaves `result.message` unset, as required.
- `HtmlJsonReportTest` now builds `rows=[]` when every extracted `jq` value is
  `None`, instead of always returning a single row - this is what makes it
  possible for that test type to produce an "empty" report at all.
- `test.custom.<module>` is unchanged; it keeps deciding for itself what a
  report looks like.
- Documented `notify_if_empty` in `docs/monitors.md`.
- Added unit tests in `tests/unit/test_monitor_tests.py` covering the default
  suppression and the `notify_if_empty` opt-in for all three test types.

Session: https://claude.ai/code/session_01SRMJB8CEDYwaG5zNxkzfWb