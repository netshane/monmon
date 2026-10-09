# ValueExpander Jinja

Allow the use of Jinja templating for value expansion in `ValueExpander` everywhere.

This update is permitted to break the existing template expansion.

The existing template expansions should be converted to Jinja context variables.

`datetime.now()` should be provided as a `now` context variable.
`today` as a datetime with the current date and time set to midnight should be provided.

provide the following context variables:
- ow, today, utcnow, yesterday, tomorrow, epoch, epoch_ms
- existing template expansions: today_iso_format, yesterday_io_format, ...

provide the globals:
- timedelta, date, datetime

provide the following filters:
- isoformat, strftime, date, epoch, epoch_ms

Use `StrictUndefined` for Jinja templating to ensure that any undefined variables raise an error.  And typos should result in `ResultStatus.ERROR`.

`utcnow` should be provided as the actual current UTC datetime not the local time in the current broken format.  This should be fixed in the implementation.

Update the samples and documentation in `monitors/sample.toml`, `monitors/examples/localhost.toml`, and  `docs/monitors.md` to reflect the new Jinja templating approach and fix any issues with range typos.

Any leftover old syntax will be expected to fail.

ex:
```toml
[test.opensearch_flag]
# Monitor will run the elastic search query in the query setting and will extract the first value
# specified by jq.  If this value exists and is greater than 0, it will generate an alert
# values delimited by {{> <}} should be evaluated
key = "network.appgroup.app.metric"  # a unique identifier for the monitor used to store results
connection = "es_prd" # this should create a connection using the dependencies method get_opensearch_service
jq = ".properties.count"
query = """
{
	"sort": [
		{
			"Timestamp": {
				"order": "desc",
				"unmapped_type": "boolean"
			}
		}
	],
	"query": {
		"bool": {
			"must": [],
			"filter": [
				{
					"match_all": {}
				},
				{
					"match_phrase": {
						"Level": "Information"
					}
				},
				{
					"match_phrase": {
						"Properties.Application": "SomeApplication"
					}
				},
				{
					"range": {
						"Timestamp": {
							"gte": "{{today_iso_format}}",
							"lte": "{{today - timedelta(days=1) | isoformat}}",
							"format": "strict_date_optional_time"
						}
					}
				}
			],
			"should": [],
			"must_not": []
		}
	}
 }
"""
```

---

## Proposed plan (review 2026-09-21)

### Assumptions taken from the spec review

- "ow" in the context variable list is read as `now`.
- The `yesterday_io_format` typo-alias is dropped; only the `*_iso_format`
  spellings are provided (old syntax is allowed to break).
- Jinja's `|` binds tighter than `-`, so the example expression
  `{{today - timedelta(days=1) | isoformat}}` parses as
  `today - (timedelta(days=1) | isoformat)` and raises. The documented form
  will be `{{ (today - timedelta(days=1)) | isoformat }}`. The sample range
  will be written with `gte` = yesterday and `lte` = today so the window is
  not empty.
- `{{ today }}` with no filter renders `str(datetime)` (`2026-09-21 00:00:00`);
  use `today_iso_format` or a filter for a specific format.

### Steps

1. `locallib/value_expander.py` - rewrite `ValueExpander` around a
   `jinja2.Environment(undefined=StrictUndefined, autoescape=False,
   keep_trailing_newline=True)`. Keep the class name, the `now_provider`
   constructor argument and the `expand()` / `expand_text()` contract so no
   caller (`monitor_test.py`, `monitor_test_factory.py`, `dependencies.py`,
   `ssa_tests.py`) changes.
   - Context built per call from `now_provider()`:
     `now`, `utcnow` (real UTC, `datetime.now(timezone.utc)`), `today`,
     `yesterday`, `tomorrow` (datetimes at midnight), `epoch`, `epoch_ms`
     (ints), and the string variants `now_iso_format`, `utcnow_iso_format`,
     `today_iso_format`, `yesterday_iso_format`, `tomorrow_iso_format`.
   - Globals: `timedelta`, `date`, `datetime`.
   - Filters: `isoformat`, `strftime(fmt)`, `date`, `epoch`, `epoch_ms`.
   - Compiled templates cached per source string; strings containing no
     `{{` / `{%` / `{#` are returned untouched. Lists, tuples and dicts are
     walked recursively as today.
2. `locallib/monitor_tests/monitor_test.py` - no change. `UndefinedError` and
   `TemplateSyntaxError` propagate out of `option()` and `run()` already maps
   any exception to `ResultStatus.ERROR`.
3. `monitors/sample.toml`, `monitors/examples/localhost.toml` - convert all
   `{{>token<}}` uses and comments to Jinja; fix the reversed range.
4. `docs/monitors.md` - replace the "Value expansion" section with a Jinja
   reference (variables, globals, filters, the precedence note, the bare
   datetime rendering note, StrictUndefined behaviour). `docs/architecture.md`
   supporting-modules row for `value_expander.py` updated.
5. `tests/unit/test_value_expander.py` - rewritten: each variable, each
   filter, `timedelta` arithmetic, nested list/dict rendering, `utcnow` is
   UTC, undefined name raises, old `{{>` syntax raises, plain strings pass
   through. `tests/unit/test_monitor_tests.py` - adjust any `{{>` fixtures and
   add a case showing a typo yields `ResultStatus.ERROR`.
6. `uv run ruff format`, `uv run ruff check --fix`, `uv run mypy`,
   `uv run pytest`.

---

## Implementation summary (2026-09-21)

Implemented as planned above, with one deviation: dropped the offset-token
support (`now-5min`, `today+2 days`, `today-7d`) that the old regex-based
expander had - Jinja arithmetic on the `datetime`/`timedelta` globals covers
the same need (`{{ (today - timedelta(days=7)) | date }}`).

- `locallib/value_expander.py` rewritten around a
  `jinja2.Environment(undefined=StrictUndefined, autoescape=False,
  keep_trailing_newline=True)`. Same class name and `now_provider` /
  `expand()` / `expand_text()` contract, so `monitor_test.py`,
  `monitor_test_factory.py` and `dependencies.py` needed no changes.
  Templates are cached by source string; a string with no `{{`, `{%` or `{#`
  is returned untouched. `utcnow` now comes from `datetime.now(timezone.utc)`
  instead of the local-time value the old code returned under that name.
- Context variables: `now`, `utcnow`, `today`, `yesterday`, `tomorrow`,
  `epoch`, `epoch_ms`, and the `*_iso_format` string variants of the
  datetimes. Globals: `timedelta`, `date`, `datetime`. Filters: `isoformat`,
  `strftime`, `date`, `epoch`, `epoch_ms`.
- `monitors/sample.toml` and `monitors/examples/localhost.toml` converted
  from `{{>token<}}` to `{{ jinja }}` syntax; the reversed opensearch_flag
  range (`lte`/`gte` swapped) fixed at the same time.
- `docs/monitors.md` "Value expansion" section rewritten as a Jinja
  reference; `docs/architecture.md`'s `value_expander.py` row updated.
- `tests/unit/test_value_expander.py` rewritten for Jinja templates,
  including a `utcnow` UTC-correctness check and cases for undefined
  variables (`UndefinedError`) and the old `{{>...<}}` syntax
  (`TemplateSyntaxError`), both surfacing as `ResultStatus.ERROR` via
  `MonitorTest.run()`. `tests/unit/test_monitor_tests.py` fixtures updated to
  the new syntax; the `verify_ssl` "typo" case split into its own test since
  a bad template now fails with a `jinja2` error rather than falling through
  to the "invalid verify_ssl value" check.
- Verified end-to-end with `uv run python monmon.py run localhost`, plus
  `uv run ruff format`, `uv run ruff check --fix`, `uv run mypy`, and
  `uv run pytest` (573 passed).

Session: 121a3332-5dc5-44cc-9def-4a4dcaefc543
