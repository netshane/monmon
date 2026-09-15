# db exists

Add a new test "test.dbexists" that runs a query and generates an alert if no rows are returned. 

If rows are returned, do not generate an alert.  Status the test as ok and include the number of rows returned as `result.value`.

The alert message should indicate that no rows were returned by the query and identify it using the key.

Create unit tests and update documentation.

```toml
[test.dbexists]
key = "network.appgroup.app.dbexists"  # a unique identifier for the monitor used to store results
connection = "db_aws" # this should create a connection using the depencies method get_db
query = """
SELECT
	a.Monitor AS [Name],
    a.ScanDate AS [ScanDate],
    a.Status AS [Status]
FROM Monitors AS a
WHERE a.Status = 'Success'
  AND a.ScanDate > DATEADD(day, -1, GETDATE())
"""
```

## Proposed plan

1. Add `DbExistsTest(DbTestBase)` to `locallib/monitor_tests/db_tests.py`, `test_type = "dbexists"`:
   - `execute()`: run the query; set `result.value = len(rows)`.
   - If `rows` is empty: add an `Alert` with a message identifying the test by `key` and stating no rows were returned (e.g. `f"No rows returned for '{self.key}'"`).
   - Else: leave status `ok` and set `result.message = f"Query returned {len(rows)} row(s)"`.
2. Export `DbExistsTest` from `locallib/monitor_tests/__init__.py` (import + `__all__`).
3. Register `"dbexists": self._db(DbExistsTest)` in `MonitorTestFactory._builders()` in `locallib/monitor_test_factory.py`.
4. Add a row to the test-type table in `docs/monitors.md`, next to `dbnorows`.
5. Add unit tests in `tests/unit/test_monitor_tests.py` mirroring `test_dbnorows_*`:
   - alerts when the query returns no rows, message includes the key
   - passes with `ok` status and `result.value` equal to row count when rows are returned
6. Run `uvx ruff format`, `uvx ruff check --fix`, `uv run pytest -m unit`.

No changes needed to the runner, service, cli, or dependencies factory — this follows the "add a monitor test type" recipe in `docs/architecture.md`.

## Implementation summary

Implemented as planned:

- `DbExistsTest` added to `locallib/monitor_tests/db_tests.py` (`test_type = "dbexists"`). Runs the query; sets `result.value` to the row count. Alerts with `No rows returned for '<key>'` when the query returns no rows; otherwise leaves status `ok` with message `Query returned <n> row(s)`.
- Exported from `locallib/monitor_tests/__init__.py` and registered as `"dbexists"` in `MonitorTestFactory._builders()` (`locallib/monitor_test_factory.py`), using the shared `_db` dependency wiring.
- Documented in `docs/monitors.md`'s test-type table.
- Unit tests added in `tests/unit/test_monitor_tests.py`: alert-on-empty and ok-with-row-count cases.
- `uvx ruff format`, `uvx ruff check --fix`, and `uv run pytest -m unit` (564 passed) all clean.

Claude session: https://claude.ai/code/session_01BMxHNUEWBALaGM4AqygFLn