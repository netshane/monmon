# Monitors database schema

The results database stores monitor run history, per-monitor scheduling state,
and the logs that back notification / email rate limiting. It is defined as
plain SQLAlchemy Core in `locallib/monitor_repository.py`, so the same code runs
against sqlite today and postgres later - only `results_db` in `settings.toml`
changes. Nothing in the schema is sqlite specific.

- `monitors.schema.sql` - raw `.schema` dump of the live sqlite database.
- Tables are created on demand by `MonitorRepository.create_schema()`
  (`metadata.create_all`); there is no migration framework.

## Tables

### `monitor_runs`
One row per monitor execution (a full `run` of a monitor's tests).

| column | type | notes |
| --- | --- | --- |
| `id` | INTEGER PK | autoincrement |
| `monitor_name` | VARCHAR(255) | indexed (`ix_monitor_runs_monitor_name`) |
| `status` | VARCHAR(32) | overall run status (`ok`, `alert`, `error`, `skipped`, ...) |
| `started_at` | DATETIME | run start |
| `finished_at` | DATETIME | null while running |
| `duration_seconds` | FLOAT | wall time of the run |
| `message` | TEXT | summary message |
| `alert_count` | INTEGER | number of alerts raised this run |
| `report_count` | INTEGER | number of reports produced this run |
| `error_count` | INTEGER | number of tests that errored this run |

### `monitor_test_results`
One row per individual test within a run. Joins back to `monitor_runs.id` via
`run_id`.

| column | type | notes |
| --- | --- | --- |
| `id` | INTEGER PK | autoincrement |
| `run_id` | INTEGER | FK-in-spirit to `monitor_runs.id`; indexed |
| `monitor_name` | VARCHAR(255) | indexed |
| `test_key` | VARCHAR(512) | identifies the test within the monitor; indexed |
| `test_type` | VARCHAR(128) | e.g. `sql`, `ping`, `http`, `docker_container_running` |
| `status` | VARCHAR(32) | per-test result status |
| `message` | TEXT | human readable result message |
| `value` | TEXT | measured value, if any |
| `error` | TEXT | error text when the test failed to execute |
| `duration_seconds` | FLOAT | test wall time |
| `result_json` | TEXT | full serialized result payload |
| `recorded_at` | DATETIME | when the row was written |

### `monitor_state`
Current scheduling / status snapshot - exactly one row per monitor
(`uq_monitor_state_monitor_name`). Drives which monitors `run-scheduled` picks
up next.

| column | type | notes |
| --- | --- | --- |
| `id` | INTEGER PK | autoincrement |
| `monitor_name` | VARCHAR(255) | unique |
| `last_run_at` | DATETIME | start of the most recent run |
| `last_status` | VARCHAR(32) | status of the most recent run |
| `last_run_id` | INTEGER | `monitor_runs.id` of the most recent run |
| `next_run_at` | DATETIME | when the monitor is next eligible to run |
| `updated_at` | DATETIME | row last modified |

### `notification_state`
Backs the repeat-notification rate limiting in `notify_throttle.py`. One row per
monitor per contact type (`uq_notification_state_monitor_type`).

| column | type | notes |
| --- | --- | --- |
| `id` | INTEGER PK | autoincrement |
| `monitor_name` | VARCHAR(255) | indexed |
| `contact_type` | VARCHAR(32) | `alert`, `report`, `info`, `notify`, `error` |
| `last_sent_at` | DATETIME | timestamp of the last delivery of this type |
| `last_status` | VARCHAR(32) | monitor status at that delivery; alert/error delays reset when it changes |
| `updated_at` | DATETIME | row last modified |

### `email_send_log`
Backs the email send quota in `email_quota.py`. One row per recipient per
successful email. Rows are pruned by `purge` after
`send_log_retention_days`.

| column | type | notes |
| --- | --- | --- |
| `id` | INTEGER PK | autoincrement |
| `recipient` | VARCHAR(320) | indexed |
| `monitor_name` | VARCHAR(255) | nullable |
| `subject` | TEXT | rendered subject |
| `sent_at` | DATETIME | indexed; delivery time, basis for the rolling windows |

## Relationships

```
monitor_runs 1 ──< monitor_test_results   (monitor_test_results.run_id -> monitor_runs.id)
monitor_runs 1 ──  monitor_state           (monitor_state.last_run_id  -> monitor_runs.id)
```

`monitor_name` is the logical key tying every table to a monitor definition in
`monitors/`. There are no database-level foreign keys; the relationships are
maintained in application code.
