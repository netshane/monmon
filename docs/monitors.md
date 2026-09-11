# Monitor file reference

Every `*.toml` file under `monitors_path` (including sub folders) is a monitor
definition. `monitors/sample.toml` is the annotated example; this file
documents the behaviour that backs it.

## [settings]

| Key | Behaviour |
| --- | --- |
| `name` | Required. The name used by `run <name>` and stored with results |
| `description`, `link` | Included in alert notifications |
| `template` | Another monitor file whose settings are merged underneath this one. Resolved relative to the monitors folder, relative to the referencing file, or as an absolute path. Templates may name templates of their own; cycles are reported |
| `monitor_type_alert` | When false, alerts raised by tests are not sent to the `alert` contacts |
| `monitor_type_report` | When true, report tables are sent to the `report` contacts |
| `active` | When false the monitor is never scheduled; `run --force` overrides it |
| `tags` | Free form labels, e.g. `tags = ["nightly", "batch"]` (a `"nightly batch"` string works too). Html reports select monitors by tag - see [reports.md](reports.md) |

Merging is a deep merge per section, and the child file always wins. Files
listed in `monitor_template_names` (by default `base.toml` and `sample.toml`)
are excluded from loading and never run as monitors themselves.

## [connections]

Added to the connections from `settings.toml` for the duration of the run.
A string value is a database connection (used by `get_db` / the db tests), a
table value with `docker_host` is a docker connection, and any other table
value with `host` and `default_index` is an opensearch connection. An empty
value means "use the connection of this name from settings".

`results_db` is always predefined - it points at the run-history database (the
`results_db` url) so a monitor can query its own history without declaring a
connection string. Setting `results_db` explicitly in `[connections]` (settings
or a monitor) overrides it. The run-history schema targets both sqlite and
postgres, so a query against `results_db` should avoid engine-specific syntax.

Run `monmon connections` (add `--json` for machine output) to list every defined
connection with secrets redacted.

```toml
[connections]
docker_local = { docker_host = "unix:///var/run/docker.sock" }
docker_windows = { docker_host = "npipe:////./pipe/docker_engine" }
docker_remote = { docker_host = "ssh://user@remotehost:22" }
```

Only `unix://`, `npipe://` and `ssh://` docker hosts are supported; a `tcp://` host is
rejected with an error because the tls material it needs (`ca.pem`,
`cert.pem`, `key.pem`) has no home in the connection table. An `ssh://` host
needs the `docker[ssh]` extra installed, a key usable without a prompt (a
scheduled run has no agent), the remote host already in `known_hosts`, and the
remote user in the remote `docker` group. Any of those failing is reported as
an error, not as an alert about the container.

## [schedule]

| Key | Behaviour |
| --- | --- |
| `cron` | Standard 5 field crontab. When set it determines the cadence and only `start_time` / `end_time` still apply |
| `daily` | Run once per day, at `start_time` if given, otherwise midnight |
| `repeat` | Re-run this long after the last run: `"5 min"`, `"30s"`, `"2 hours"`, `"1 day"` |
| `days_of_week` | Space or comma delimited: `"S M Tu W Th F Sa"`. Runs that would land on another day move to the next allowed day |
| `start_time`, `end_time` | Time of day window, `HH:MM` or `HH:MM:SS`. A run before the window moves to the window start; after it moves to the next day |
| `recheck_after_skipped`, `recheck_after_error`, `recheck_after_alert` | Re-run this long after a run that ended in that status, instead of on the normal cadence |
| `recheck_max_attempts` | How many times a failing monitor is rechecked before it goes back on its normal schedule |

A monitor with no `cron`, `repeat`, `daily`, or `days_of_week` is manual only -
it never appears as due and is run by name.

The next run is computed from the last stored run time, so nothing is lost if
the scheduler does not fire for a while. `next-runs` shows the calculation and
stores it in `monitor_state.next_run_at`.

### Rechecks

A monitor that finishes `skipped`, `error`, or `alert` has not settled, so
leaving it until its next scheduled slot can mean a long wait to find out it
recovered. Each of those statuses has a `recheck_after_<status>` delay that
takes the monitor **off** its normal cadence: the next run becomes
`last_run + recheck_after_<status>`, and it stays on that cadence until the
monitor runs `ok` again.

```toml
[schedule]
repeat = "5 min"
recheck_after_error = "1 hour"   # a broken monitor backs off to hourly
recheck_after_alert = "30s"      # an alerting one is rechecked quickly
recheck_max_attempts = 3
```

- Intervals use the same format as `repeat` (`"30s"`, `"5 min"`, `"2 hours"`).
  A **bare number means minutes**, so `recheck_after_error = 60` is an hour.
- The delay replaces the normal schedule rather than shortening it, so it can
  either speed a monitor up or back it off, as above.
- Rechecks still honour `start_time` / `end_time` / `days_of_week`; a recheck
  landing outside the window moves to the next opening. On a `cron` monitor
  the recheck is held to a day the cron expression itself would fire on
  (day-of-week, day-of-month and month all count), so a weekday-only monitor
  is never rechecked at the weekend. It keeps its own time of day within that
  day; a recheck pushed onto a later day runs at `start_time`, or midnight
  when no window is set.
- Manual monitors never recheck - they have no schedule to come off.
- `0` turns a recheck off. An unset value in a monitor inherits the
  `[schedule]` default from `settings.toml`; an explicit `0` overrides that
  default and turns the recheck off for this monitor alone. An unparseable
  interval is logged and treated as `0`.
- A recheck is an ordinary run, so it notifies as any other run does - use the
  `[contact]` renotify delays below to stop a failing monitor notifying on
  every recheck, and the `[email]` send quota to bound the total it can send.
- Every `skipped` result qualifies, whether the skip came from
  `skip_on_parent_fail` or from every test in the monitor skipping. A monitor
  skipped by its parent will keep skipping until the parent recovers, so a
  `recheck_after_skipped` shorter than the parent's own cadence just burns
  attempts.

`recheck_max_attempts` caps how many rechecks happen before the monitor is put
back on its normal schedule (`0` or unset = no cap). The attempt count is the
number of consecutive non-`ok` runs since the monitor last ran `ok`, less the
run that first failed - so `recheck_max_attempts = 3` gives three rechecks
after the initial failure, four runs in total. It is derived from run history
rather than stored, so it resets by itself when the monitor recovers; note
that `purge` discarding an old `ok` run, and `--simulate` storing no runs at
all, both feed into it. One cap covers all three statuses.

## [hierarchy], [hierarchy.A], [hierarchy.B], ...

Optional. `[hierarchy]` is the primary hierarchy; each `[hierarchy.X]` is an
independent alternate hierarchy handled the same way and evaluated separately.

| Key | Behaviour |
| --- | --- |
| `node_name` | The node this monitor provides. Optional: defaults to the monitor name for `[hierarchy]`, or `<monitor name>-<hierarchy name>` for an alternate (e.g. `my-monitor-A`). A root node with no `parent_node` must set it explicitly - a section with neither key fails to load |
| `parent_node` | The node this monitor depends on. A `node_name` from another monitor in the same hierarchy. A `parent_node` equal to this hierarchy's own (possibly defaulted) `node_name` is treated as a root node, with a warning |
| `skip_on_parent_fail` | Do not run when a monitor providing the parent node last alerted or errored. The skip is stored so history shows why |
| `alert_on_parent_fail` | Run as normal, but add an alert saying the parent failed |

`validate` (and every `run-scheduled`) warns about parents that no monitor
provides, self-parenting, and cycles. `run-scheduled` runs parents before
children so a child sees its parent's fresh status.

## [contact]

Each contact type is a list of `<channel>:<target>` strings:

```toml
[contact]
alert = [
    "email:shane@netshane.com",
    "email:Ops Team:ops@example.com",
    "slack:#monitoring",
    "slack:@shane",
    "teams:#monitoring",
    "teams:@shane",
    "text:+14155551212",
]
```

| Key | Behaviour |
| --- | --- |
| `alert` | Alerts go here (needs `monitor_type_alert`) |
| `report` | Report tables plus the messages of all tests, when a report monitor completes (needs `monitor_type_report`) |
| `error` | Sent when a test errors |
| `info` | Every run: per test outcomes plus any report tables |
| `notify` | Every run: a short monitor name / result / run time notice |
| `combine_alerts` | Hold alerts back and send one message per contact at the end of the run |
| `combine_reports` | The same for report tables |
| `renotify_after_alert`, `renotify_after_report`, `renotify_after_error`, `renotify_after_info`, `renotify_after_notify` | Minimum gap before this monitor sends another notification of that type |

Contact rules, enforced when the monitor is loaded - a monitor with an invalid
contact fails to load and the reason is reported by `validate`:

- `email:<address>` or `email:<name>:<address>`, and the address must be well formed
- `slack:` and `teams:` targets must start with `#` (a channel) or `@` (a person)
- `text:<phone_number>` must look like a permissive E.164 number (digits, an
  optional leading `+`, and optional spaces/dashes/parentheses)

Batching is per contact type and channel: each contact receives its own
combined message. A send that fails is logged and the remaining contacts are
still notified. With no smtp `host` configured, emails are logged instead of
sent; the same applies to slack and teams with no credentials in
`settings.toml`. Notifications are plain text (email also carries an html
table alternative for reports).

`text` contacts are sent through the provider configured in `settings.toml`
`[text] provider` (`imsg`, sent through the `imsg` cli tool - macOS only - or
`twilio`, sent through the Twilio api). With no provider configured, texts are
logged instead of sent. A text body over 160 characters is truncated, with a
warning logged, regardless of provider.

### Rate limiting repeat notifications

A monitor left in `error` or `alert` is rechecked on its
`recheck_after_<status>` cadence, and every one of those runs would otherwise
notify again. `renotify_after_<type>` sets the minimum gap between repeat
notifications of one type for one monitor:

```toml
[contact]
alert = ["email:ops@example.com"]
renotify_after_alert = "1 hour"   # one alert an hour while it keeps failing
renotify_after_report = "12h"     # a daily-ish report even on a fast cadence
```

- The delay uses the same syntax as the recheck intervals, and a **bare number
  means minutes**.
- The window is fixed and measured from the last *successful* delivery. A
  message that failed, that was dropped by the `[email]` send quota, or that
  only reached a logging sender because a channel has no credentials does not
  start the window - so a misconfigured channel cannot silence the
  notifications behind it.
- `0` turns the rate limiting off. An unset value in a monitor inherits the
  `[contact]` default from `settings.toml`; an explicit `0` overrides that
  default for this monitor alone. An unparseable interval is logged and
  treated as `0`.
- The limit is per monitor and per contact type, not per contact: when a type
  is held back, none of its contacts hear about that run. The five types have
  five independent timers.
- `alert` and `error` also reset whenever the monitor's status changes, so a
  recovery, or a slide from `alert` to `error`, is reported immediately.
  `report`, `info`, and `notify` fire on healthy runs too, so they are plain
  fixed windows with no status reset - resetting them on every `ok` would
  leave them never rate limited.
- Alert identities are not tracked. A monitor already inside its alert window
  stays quiet even if the next run raises a completely different alert.
- Held back notifications are logged at info level, naming the monitor, the
  type, and when the next one is due. With `combine_alerts` / `combine_reports`
  the suppression happens before the fragment joins the batch, so a held back
  monitor is absent from the combined message and from its subject counts.
- `--simulate` never consumes or advances a window, and `test-notify` always
  bypasses the rate limiting.

The timers live in the `notification_state` table and are cleaned up by `purge`
and `purge-monitor`.

`monmon contacts <monitor>` lists the resolved contacts of a monitor, and
`monmon test-notify <monitor> <type>` sends a test notification to one list.

The older `alert_email` / `combine_alert_emails` style keys have been removed;
a monitor still using them fails to load with a message naming the replacement.

## [test.*]

Each section is one test. `key` is the unique identifier stored with the
result. Any string value may contain `{{>token<}}` placeholders (see below).

### Instances

A section is written `[test.<test_type>.<instance>]`, where the instance name
is yours to choose. That is how a monitor runs several tests of the same type:

```toml
[test.html_200.home]
key = "network.appgroup.app.home"
url = "https://example.com/"

[test.html_200.health]
key = "network.appgroup.app.health"
url = "https://example.com/health"
```

A `custom` test carries its module in the test type, so its instance is one
segment further along: `[test.custom.magic_py.orders]`.

The instance name is optional - `[test.ping]` is the same as writing
`[test.ping.default]` - but new sections should name one, since a type written
on its own can only appear once in a monitor.

- `key` is optional. Without one the key is `<monitor>.<test_type>.<instance>`,
  so an unkeyed `[test.ping]` is stored as `<monitor>.ping.default`.
- Instances do not inherit options from the bare `[test.<test_type>]` section;
  each instance stands alone.
- An option whose value is a table - `headers` on the `html_*` tests - only
  works inside an instance section. Written under a bare `[test.<test_type>]`
  it is indistinguishable from an instance name and is loaded as one.
- Use `[test.<test_type>.<instance>]` sections, never `[[test.<test_type>]]`
  arrays. An array of tables is reported by `validate` and its tests are not
  loaded.

### Duplicate keys

Within one monitor, if two tests share a `key` only the last one loads; the
earlier test is skipped and reported by `monmon validate`. Template sections
are merged underneath the monitor's own, so a monitor replaces a test it
inherited by reusing that test's key.

A key used by more than one monitor is also reported by `validate`, but
nothing is skipped - both tests still run. Keys are matched by report field
selectors across monitors, so sharing one makes a report ambiguous.

| Section | Behaviour |
| --- | --- |
| `[test.ping]` | Pings `server` and/or `servers`; alerts per unreachable host |
| `[test.dbflag]` | Runs `query` on `connection`; alerts when the scalar is greater than 0 |
| `[test.dbthreshold]` | Query returns `Name`, `Value`, optional `Details`; alerts per row whose value exceeds `threshold` |
| `[test.dbnorows]` | Alerts when the query returns any rows; the rows are listed in the alert details (`max_rows`, default 50) |
| `[test.dbreport]` | Reports every column the query returns as a table |
| `[test.docker_container_running]` | Alerts per `container_name` / `container_names` entry that is not a running container on `connection` |
| `[test.ssa_job_succeeded]` | Alerts unless the named SQL Server Agent job's most recent run in `timeframe` succeeded or is in progress |
| `[test.ssa_job_errors]` | Reports any failed runs of the named SQL Server Agent job in `timeframe` |
| `[test.opensearch_flag]` | Runs the search on `connection`, takes the first value at `jq`, alerts when it is greater than 0 |
| `[test.elasticsearch_report]` | Runs the search and reports one row per hit with a column per path in `jqs` |
| `[test.html_200]` | Fetches `url` (following redirects) and alerts on any status other than 200 (or `status_code`) |
| `[test.html_json_exists]` | Fetches `url` as json and alerts when `jq` resolves to nothing |
| `[test.html_json_value]` | As above, but alerts when the value does not equal `value` |
| `[test.custom.<module>]` | Calls `command` in `<module>.py` under `custom_tests_path` with the expanded `args` |

`opensearch_flag` / `elasticsearch_report` also accept an `index` to override
the connection's `default_index`. `html_*` tests accept `headers`.

`docker_container_running` names containers with `container_name` and/or
`container_names`, and passes only when every one of them is running. A
container that does not exist, or whose state is anything but `running`,
raises one alert naming its state; the alert details carry the exit code,
start time, and restart count. `connection` is a docker connection name from
`[connections]` - without one the local docker engine is used: `[docker] host`
in `settings.toml`, which defaults to `unix:///var/run/docker.sock`, or
`npipe:////./pipe/docker_engine` when the monitor host is windows. Two options
adjust what counts as running:

| Option | Default | Behaviour |
| --- | --- | --- |
| `require_healthy` | `false` | Also alert when a running container's healthcheck reports `unhealthy` |
| `allow_restarting` | `false` | Treat a `restarting` container as running rather than alerting |

Docker being unreachable - not installed, daemon down, ssh refused, an unknown
connection name - is an **error**, not an alert, so it is routed to the `error`
contacts rather than reported as a container failure.

`dbreport`, `elasticsearch_report`, `html_json_report`, and `ssa_job_errors`
accept `notify_if_empty` (default `false`). A report with no rows is not sent
unless `notify_if_empty` is `true`.

`ssa_job_succeeded` and `ssa_job_errors` connect to `connection` and query
`msdb.dbo.sysjobhistory` for `job_name`, restricted to runs since `timeframe`
ago (an interval string like `"2 hours"`, default `"1 day"`). Both require
the connection to point at the SQL Server instance running the job - they are
not portable to other database engines. `ssa_job_succeeded` alerts when the
job's latest run in the window is missing, or has any status other than
Succeeded or In Progress. `ssa_job_errors` reports every failed run found in
the window.

`ssa_job_succeeded`'s "In Progress" check relies on SQL Server Agent having
already written the job-level (`step_id = 0`) history row for the current
run, which it normally does not do until the job finishes. Set `timeframe`
comfortably longer than the job's typical run time - a `timeframe` shorter
than a still-running job's duration will report "no run found" instead of
recognizing it as in progress.

A custom command returns:

* `None` - the test passes
* a dict - an alert (`message`, `name`, `value`, `threshold`, `details`; other
  keys are folded into the details)
* a list of dicts - a report whose columns come from the dict keys
* a list of lists - a report using the `columns` option for headers

`custom_tests/example_check.py` shows both shapes.

### jq paths

`jq` / `jqs` accept a practical subset of jq: `.a.b.c`, `.a[0].b`, `.a[].b`,
`.["odd key"].b`, and `.` for the document itself. Keys match case sensitively
first, then case insensitively. `opensearch_flag` looks in each hit's
`_source` first and then the whole response, so aggregation paths such as
`.aggregations.total.value` also work.

### Value expansion

Anywhere in a test's string values, `{{>token<}}` is replaced:

| Token | Example result |
| --- | --- |
| `now`, `now_iso_format` | `2026-08-12T10:30:15` |
| `today`, `yesterday`, `tomorrow` | `2026-08-12` |
| `today_iso_format`, `yesterday_iso_format` | `2026-08-12T00:00:00` |
| `utcnow`, `utcnow_iso_format` | UTC equivalents |
| `epoch`, `epoch_ms` | `1786786215` |
| `strftime:%Y/%m/%d` | `2026/08/12` |
| `today-7d`, `now-5min`, `today+2 days` | the base token with an offset |

`yesterday_io_format` is accepted as a spelling of `yesterday_iso_format`.
Unknown tokens are left in place and logged.

## Result statuses

| Status | Meaning |
| --- | --- |
| `ok` | Every test passed |
| `alert` | At least one test raised an alert |
| `error` | At least one test could not be run or failed unexpectedly |
| `skipped` | Skipped, e.g. a failed parent or `--simulate` |
| `inactive` | `active = false` |

A monitor's status is the worst status across its tests: `error` outranks
`alert`, which outranks `ok`.
