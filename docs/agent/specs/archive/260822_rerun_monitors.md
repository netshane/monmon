# bug: rerun skipped monitors

- if a monitor is skipped, it should be pulled off the normal schedule and retried after a configurable delay
	- there should be an option configurable in `settings.toml` that can be overriden in monitor definitions called `recheck_after_skipped` which takes a value in a human time format using `parse_interval`
- if a monitor is in error status it should be pulled off the normal schedule and retried after a configurable delay
	- there should be an option configurable in `settings.toml` that can be overriden in monitor definitions called `recheck_after_error` which takes a value in a human time format using `parse_interval`
- if a monitor is in alert status, it should be pulled off the normal schedule and retried after a configurable delay
	- there should be an option configurable in `settings.toml` that can be overriden in monitor definitions called `recheck_after_alert` which takes a value in a human time format using `parse_interval`

- the delay should override the normal schedule of the monitor.  So if the monitor is set up `repeat = "5 min"` and `recheck_after_error = "1 hour"` it should only run on the 1 hour delay until it is no longer in error status.

- a bare number for `recheck_after_*` should use the default behavior of `parse_interval` which is minutes

- alerts should renotify.  There will be a follow up feature to address excessive alerting.

- errored manual monitors should not start auto-retrying

- setting a value of 0/unset should turn the feature off and use the normal schedule.

- retries should respect start_time / end_time / days_of_week

- add a `recheck_max_attempts` option to limit the maximum number of retries.  This should be overridable in monitor definitions.  A value of 0/unset should mean unlimited retries.
	- this counter should reset when the monitor goes to `ok` status.
	- once the maximum number of retries is reached, the monitor should be put back on its normal schedule and the next run should be treated as a normal run (not a retry).
	- this counter should be global across all recheck types
	- the attempt count is the number consecutive non-ok runs since the monitor last ran ok, minus the initial failure.  So `recheck_max_attempts = 3` means the monitor will be retried 3 times after the initial failure, for a total of 4 runs before it is put back on its normal schedule.

- an error from parse interval should be logged and treated as 0/unset

- don't store the max attempts counter, derive it from previous runs.


---

## Implementation summary

Claude session: `session_0116bRe3MuBDsXXjmE4zKWqE`

A monitor whose last run ended `skipped`, `error`, or `alert` now comes off its
normal cadence: `next_run` becomes `last_run + recheck_after_<status>` until it
runs `ok` again or the attempt cap is used up.  The whole decision lives in
`ScheduleCalculator.next_run()`; the runner, notifier, and cli are untouched,
so a recheck is an ordinary run and notifies exactly as any other run does.

### Changes

| File | Change |
| --- | --- |
| `locallib/monitor_models.py` | `ScheduleConfig` gains `recheck_after_skipped` / `_error` / `_alert` (raw strings) and `recheck_max_attempts`; new `_clean_int` helper |
| `locallib/schedule_calculator.py` | `RECHECK_STATUSES`; constructor takes the settings defaults; `next_run()` / `is_due()` take `last_status` and `attempt_count`; `_next_recheck` / `_recheck_delay` / `_recheck_max_attempts` |
| `locallib/monitor_repository.py` | `consecutive_failure_counts()` - runs per monitor since its last `ok`, one bulk query |
| `locallib/monitor_service.py` | `schedule_entries()` reads the streak once and passes `last_status` + `attempt_count` through |
| `locallib/dependencies.py` | `MonitorSettings.schedule`; `get_schedule_calculator()` parses the defaults, `_recheck_default()` logs bad intervals |
| `settings.toml` | new `[default.schedule]` section, all four keys defaulting to off |
| `docs/monitors.md`, `docs/architecture.md`, `README.md`, `monitors/sample.toml` | reference and examples |

### Decisions worth remembering

- **The recheck replaces the cadence, it does not shorten it**, so the same
  knob expresses both a fast recheck and a backoff.  It is applied before the
  normal branch in `next_run()` and still goes through
  `_advance_into_window()`, so `start_time` / `end_time` / `days_of_week` hold.
- **No schema change.**  The attempt count is derived from `monitor_runs`
  (rows since the monitor's last `ok`) rather than stored, because
  `create_schema()` is `metadata.create_all()` and would not add a column to an
  existing `monitors.db`.  It also resets by itself on recovery.  Two knock-on
  effects, both accepted: `purge` dropping an old `ok` run and `--simulate`
  storing no runs both feed into the count.
- **`None` and `0` differ in a monitor's `[schedule]`.**  Unset inherits the
  `settings.toml` default; an explicit `0` overrides it and turns the recheck
  (or the attempt cap) off for that monitor alone.  This is why the fields are
  `str | None` / `int | None` rather than plain values.
- **Attempt count is the failure streak minus one** - the run that first failed
  is not itself a retry.  The subtraction happens in
  `MonitorService.schedule_entries()`, so the calculator compares like with
  like against `recheck_max_attempts`.
- **Every `skipped` result qualifies**, not just `skip_on_parent_fail`.  Only
  the bare status string reaches the scheduler, so the two skip sources are
  indistinguishable without new state.
- Intervals go through the existing `parse_interval`, so a bare number means
  **minutes**.  An unparseable value is logged and treated as off, at both the
  settings and the monitor level.

### Tests

`tests/unit/test_schedule_calculator.py` (13 recheck cases: per status, ok
keeps the cadence, no default configured, monitor override, explicit `0`,
unparseable, bare number, time window, manual monitor, never-run monitor, the
cap, opting out of the cap), `tests/unit/test_monitor_repository.py` (5 streak
cases), `tests/unit/test_monitor_service.py` (3 end-to-end scheduling cases).

### Post review fixes

A code review of the diff surfaced three defects, all fixed:

- `dependencies.get_schedule_calculator()` called bare `int()` on
  `recheck_max_attempts`, so a non-numeric settings value raised `ValueError`
  on the path of **every** cli command.  Now `_recheck_max_attempts()` logs and
  falls back to 0 (uncapped), matching how `_recheck_default()` treats a bad
  interval.  Negative values clamp to 0.
- `_clean_int` caught only `ValueError`, so `recheck_max_attempts = "inf"`
  raised an uncaught `OverflowError` during monitor load, and a typo was
  silently swallowed.  It now catches both and logs the field name.
- A recheck on a `cron` monitor ignored the cron's day restriction: the
  candidate is `last_run + delay`, not a cron occurrence, and
  `_advance_into_window` deliberately skips day filtering when `cron` is set.
  A weekday-only monitor failing on Friday was rechecked on Saturday.
  `_cron_allows_date()` now holds a recheck candidate to a date the expression
  actually fires on - covering day-of-month and month as well as weekday - and
  is applied only to recheck candidates, so normal cron scheduling is
  untouched. An unparseable expression allows the day rather than dropping the
  recheck; `_next_cron` already reports it on the normal path.
- `consecutive_failure_counts()` was run on every `schedule_entries()` call -
  twice per `run-scheduled` - even with the feature off everywhere, scanning
  `monitor_runs` for a result that was then discarded.
  `ScheduleCalculator.rechecks_possible()` now gates the query on any recheck
  being configured, in settings or on any monitor.

Full unit suite passes apart from
`test_opensearch_report_builds_one_row_per_hit`, which fails on `main` as well
and is unrelated to this change.
