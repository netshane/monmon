# ssa tests

Add two new tests for monitoring the status of SQL Server Agent (SSA) jobs.

Docs and unit tests should be added to support the new tests.

## SSA Job Succeeded
```toml
[test.ssa_job_succeeded]
key = "aws_db.MyJobName.succeeded"
connection = "db_aws"
job_name = "MyJobName"
timeframe = "2 hours"  # default to 1 day
```

- connect to a database server and use the query below to determine if the named job succeeded.
- this test should generate an alert
- this query should only check the most recent run of the job in the given timeframe
- if the query comes back with a status other than "Succeeded" or "In Progress" the test should raise an alert
- if the query returns no rows, the test should raise an alert
```sql
            SELECT 
                TOP 1
                j.name AS JobName,
                h.run_status AS RunStatus,
                h.run_date AS RunDate,
                h.run_time AS RunTime,
                CASE h.run_status
                    WHEN 0 THEN 'Failed'
                    WHEN 1 THEN 'Succeeded'
                    WHEN 2 THEN 'Retry'
                    WHEN 3 THEN 'Canceled'
                    WHEN 4 THEN 'In Progress'
                END AS RunStatusDescription
            FROM msdb.dbo.sysjobs j
               JOIN msdb.dbo.sysjobhistory h ON j.job_id = h.job_id
            WHERE j.name = :job_name
              AND h.step_id = 0 -- only check the job outcome, not the individual steps
              AND (h.run_date * 1000000 + h.run_time) >= (CAST(CONVERT(varchar, :timeframe_datetime, 112) AS int) * 1000000 + CAST(CONVERT(varchar, :timeframe_datetime, 108) AS int))
            ORDER BY 
               h.run_date DESC, 
               h.run_time DESC;
```


## SSA Job Errors
```toml
[test.ssa_job_errors]
key = "aws_db.MyJobName.errors"
connection = "db_aws"
job_name = "MyJobName"
timeframe = "2 hours"  # default to 1 day
notify_if_empty = false  # default value, set to true to notify even if no rows
```

- connect to a database server and use the query below to generate a report of any failed runs for the named job in the given timeframe.

```sql
            SELECT 
                j.name AS JobName,
                h.run_date AS RunDate,
                h.run_time AS RunTime,
                CASE h.run_status
                    WHEN 0 THEN 'Failed'
                    WHEN 1 THEN 'Succeeded'
                    WHEN 2 THEN 'Retry'
                    WHEN 3 THEN 'Canceled'
                    WHEN 4 THEN 'In Progress'
                END AS RunStatus,
                h.message AS RunMessage
            FROM msdb.dbo.sysjobs j
              JOIN msdb.dbo.sysjobhistory h ON j.job_id = h.job_id
            WHERE j.name = :job_name
              AND h.run_status = 0
              AND h.step_id = 0 -- only check the job outcome, not the individual steps
              AND (h.run_date * 1000000 + h.run_time) >= (CAST(CONVERT(varchar, :timeframe_datetime, 112) AS int) * 1000000 + CAST(CONVERT(varchar, :timeframe_datetime, 108) AS int))
            ORDER BY 
               h.run_date DESC, 
               h.run_time DESC;
```
## Implementation summary

- Added `locallib/monitor_tests/ssa_tests.py`:
  - `SsaJobTestBase` (extends `DbTestBase`) computes the cutoff from
    `job_name` + `timeframe` (`helpers.parse_interval`, default `1 day`) and
    binds it as separate `cutoff_date`/`cutoff_time` int params (`YYYYMMDD`
    / `HHMMSS`) rather than a single `:timeframe_datetime`, so no
    `CONVERT`/`CAST` is needed in the query and the params are testable
    without a real SQL Server connection.
  - `SsaJobSucceededTest` (`ssa_job_succeeded`) fetches the job's latest run
    regardless of status and alerts when there is no run in the window or
    the status isn't `Succeeded`/`In Progress`.
  - `SsaJobErrorsTest` (`ssa_job_errors`) reports failed runs in the window,
    honoring `notify_if_empty` the same way `dbreport` does.
- Registered both types in `MonitorTestFactory._builders()` (via the
  existing `self._db(...)` helper) and exported them from
  `locallib/monitor_tests/__init__.py`.
- Documented both sections in `docs/monitors.md`, including the
  `notify_if_empty` note and a callout that they only work against a SQL
  Server connection.
- Added unit tests in `tests/unit/test_monitor_tests.py`. Because the
  queries use SQL Server-only syntax (`TOP 1`, `msdb.dbo.*`) that sqlite
  can't execute, added a lightweight `FakeSsaDbFactory`/`FakeSsaConnection`
  that returns canned columns/rows without parsing the SQL text, rather than
  reusing the sqlite-backed `FakeDbFactory` used by the other db tests.

Session: https://claude.ai/code/session_01SRMJB8CEDYwaG5zNxkzfWb
