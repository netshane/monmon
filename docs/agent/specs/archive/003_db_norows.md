Create a new monitor that runs a query and checks that no rows are returned.
If any rows are returned, it should generate an alert.
All the returned rows should be included in the error message of the alert.

A sample monitor configuration is provided below.

```toml
[test.dbnorows]
key = "app.techprofile-errors"
connection = "db_aws_app"
query = """
SELECT 
    RepId,
    LastRunMessage
FROM exdata_VZN5G_Profile
WHERE LastRunMessage = 'Failed'
"""
```

## Implementation summary

Implemented 2026-08-12 (Claude Code session `68fbf1e7-2bdf-48e0-aeef-b7c1271cf29b`).

**New test type** `[test.dbnorows]` - `DbNoRowsTest` in
`locallib/monitor_tests/db_tests.py`, extending the existing `DbTestBase` so it
shares `connection` / `query` handling and token expansion with the other db
tests.

- No rows: passes with the message `Query returned no rows`.
- Any rows: one `Alert` whose `message` is the one line summary
  `Query returned N row(s)` and whose `details` carries the column header plus
  the returned rows, pipe delimited, one row per line. `value` is the row count,
  `None` renders as empty, and newlines / `|` inside a value are flattened so a
  row always stays on its own line.
- `max_rows` (default 50) caps the listed rows; the rest become a
  `... and N more row(s)` tail. The count reported is always the full count.
- `result.value` is the row count either way, and `result.message` is the same
  one line summary so per-test summaries and stored results stay single line.

**Wiring** - exported from `locallib/monitor_tests/__init__.py` and mapped to
the `dbnorows` type in `MonitorTestFactory._builders()`. The sample section name
above was `[test.db_norows]`; the section name is stored verbatim as the result's
`test_type` and report fields match on it, so a single canonical spelling is used
rather than an alias.

**Docs** - row added to the test table in `docs/monitors.md`; annotated
`[test.dbnorows]` example added to `monitors/sample.toml`.

**Report rendering** - the alert details cell in `reports/default/template.html.j2`
now uses `white-space: pre-wrap` so the row listing keeps its line breaks.

**Verification:** 208 unit tests pass (4 new: the alert lists the rows, the empty
result passes, values are flattened, and `max_rows` truncates),
`uvx ruff format` / `uvx ruff check --fix` clean.

Reviewed in `docs/review/003_db_norows_review.md`; all five findings are fixed
in this branch.
