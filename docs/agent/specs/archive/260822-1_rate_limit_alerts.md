# feat: rate limit alerts and reports
- a monitor that is in error or alert will be retried periodically.  However, it should not always send out alerts and reports every time it retries
- Add an option `renotify_after_alert` that specifies how long must pass before an alert notification is sent out for a monitor that has already alerted before alert notifications are sent out again
- Add an option `renotify_after_report` that specifies how long must pass before a report notification is sent out for a monitor that has already sent out a report before report notifications are sent out again
- these values should be in a human readable form parsed by `parse_interval`

- these options should be configurable in `settings.toml` and overridable in monitor definitions.  A None value will inherit the default and an explicit 0 value will turn off the rate limiting and send out notifications on every retry.

- these counters should reset when the monitor goes to `ok` status.

- options to cover error, notify, and info should be added as well.

- these options should apply at the monitor level, not the contact level.  So if a monitor has multiple contacts, the rate limiting applies to all contacts for that monitor.

- blocked notifications should be logged at the info level with a message indicating that the notification was blocked due to rate limiting.

- for combine_alerts / combine_reports, fragments should be suppressed before they are combined.

- `--simulate` runs should not consume or advance these counters

- dropped notifications should not count towards the EmailQuotaGate
- `test-notify` should always bypass throttling
- `purge` / `purge-monitor` should clean up any new states introduced

- these options should be added to the `[contact]` section next to `combine_alerts` and `combine_reports`.

- the delay should be measured from the last successful delivery of a notification.
- only `live` or `sent` deliveries should count towards the delay.  Failed deliveries should not count towards the delay. Log only deliveries should not count.

- for `alert` and `error`, the delay counters should reset based on changes to monitor status:
    - returning `ok` should reset the counters and allow notifications to be sent again.
    - any change in the monitor status e.g. `alert` to `error` should reset the counters

- changes to status should not apply to `report`, `info`, or `notify` counters.  These delays should be measured from the last successful delivery of a notification of that type.

- if a monitor raises a different alert on a subsequent run, it should still be suppressed and alert identities do not need to be tracked
- this should be fixed window delay



---

## Implementation summary

Claude session: `session_0116bRe3MuBDsXXjmE4zKWqE`

Repeat notifications are now rate limited by a gate the `Notifier` consults
before it renders or queues anything, mirroring how `EmailQuotaGate` sits in
front of the email sender.

**New module** - `locallib/notify_throttle.py`

- `NotifyThrottle` holds the resolved `renotify_after_<type>` defaults, one per
  contact type.  It reads no settings; `dependencies` injects it.
- `NotifyThrottleGate.allow()` answers whether a notification is due,
  `record()` starts the window, and `reset()` clears the status driven types
  when a monitor recovers.  `clock` is injected so tests move the window
  without sleeping, and per-monitor state is cached for the length of a run.
- `STATUS_RESET_TYPES = ("alert", "error")` - only those two reset on a status
  change.  `report`, `info`, and `notify` fire on healthy runs too, so
  resetting them on every `ok` would leave them never rate limited.

**Persistence** - `locallib/monitor_repository.py`

- New `notification_state` table, unique on `(monitor_name, contact_type)`,
  holding `last_sent_at` and the `last_status` at the time of that delivery.
- `get_notification_state()`, `record_notification()`,
  `clear_notification_state()`.
- `purge_monitor()` drops a monitor's rows; `purge_runs_before()` drops rows
  whose last delivery predates the cutoff.

**Configuration**

- Five `renotify_after_<type>` fields on `ContactConfig`, plus a
  `renotify_after(contact_type)` accessor.  Values are kept as written and
  parsed by the gate, so `None` inherits and an explicit `0` disables locally -
  the same contract as `recheck_after_<status>`.
- `[default.contact]` defaults in `settings.toml`, read by
  `dependencies.get_notify_throttle()` through `_renotify_default()`, which
  logs and ignores an unparseable interval.

**Notifier** - `locallib/notifier.py`

- Optional `throttle_gate`; `None` leaves everything unthrottled, which is what
  `--simulate` and a hand built notifier get.
- `notify()` calls `gate.reset()` first on an `ok` result.
- `_allowed()` is checked before rendering, so a held back monitor never joins
  a combined batch and never skews its subject counts.
- `_record()` starts the window only for a delivery that was `live`, `sent`,
  and not `throttled` - a failure, a quota drop, or a logging-only sender must
  not silence what follows.  `_record_batch()` records every monitor that
  contributed to a combined message; `_Fragment` carries the status for it.
- `send_test()` reaches `_render` / `_deliver` directly, so `test-notify`
  bypasses the rate limiting and records nothing.

**Refactor** - `parse_interval` moved to `locallib/helpers.py`;
`ScheduleCalculator.parse_interval` delegates to it, so the throttle shares the
syntax without depending on the scheduler.

**Docs** - `docs/monitors.md` gains a "Rate limiting repeat notifications"
section and the `[contact]` table rows (and the stale "a recheck notifies
exactly as any other run does" line is corrected); `README.md` settings table;
`docs/architecture.md` data model, notification layer, and module table.

**Tests** - new `tests/unit/test_notify_throttle.py` (25 cases: window
mechanics, per-type and per-monitor isolation, status reset rules, override
precedence, and the notifier-level combined/simulate/test-notify behaviour),
plus additions to `test_monitor_repository.py` and `test_monitor_models.py`.
467 unit tests pass; `test_monitor_tests.py::test_opensearch_report_builds_one_row_per_hit`
fails both before and after this change and is unrelated.

**Post-review fixes** (from `/code-review` on the working tree)

- `parse_interval` now returns `None` instead of raising `OverflowError` on an
  interval too large for a `timedelta`.  It was reachable from both new config
  surfaces and would have taken down `get_notifier()` - and so the whole run -
  despite both the docs and `_renotify_default` promising a fail-soft ignore.
- `purge_runs_before` drops `notification_state` rows only once the monitor has
  aged out of `monitor_state`, rather than on the run-history cutoff.  The
  timers belong to a monitor, not to its run history; purging them on the
  cutoff silently truncated any window longer than the purge retention.
- `NotifyThrottleGate.reset()` takes the monitor's overrides and skips the
  write entirely when no status driven type is rate limited, so a healthy run
  under the shipped all-zero defaults costs no database write.  The unused
  `NotifyThrottle.enabled` mirror of `EmailQuota.enabled` was removed.
- Every new test carries `@pytest.mark.unit`; without it `pytest -m unit`
  silently skipped all 32 of them.

**Known and accepted** - `record_notification` uses the same non-atomic
count-then-insert as the existing `monitor_state` upsert, so two overlapping
`run-scheduled` processes hitting one monitor and contact type could race on
`uq_notification_state_monitor_type`.  Consistent with the codebase, and the
application is a single cron driven batch.

**Deliberately not done** - alert identities are not tracked, per the spec, and
suppressed notifications produce no `Delivery` record (nothing was attempted);
they are logged at info level instead.
