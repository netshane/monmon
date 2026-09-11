# Html report reference

`generate-report` renders stored monitor history into static html pages. The
pages are self contained - all css and js is inlined, nothing is fetched over
the network - so they work when opened straight off disk with `file://`.

```sh
# reports/default -> output/default/index.html
uv run monmon.py --dev generate-report

# one named report
uv run monmon.py --dev generate-report --template nightly

# every report folder, in one run
uv run monmon.py --dev generate-report --all

# somewhere other than output/
uv run monmon.py --dev generate-report --output /srv/www/monitors

# fail the render instead of silently emitting blanks for unknown variables
uv run monmon.py --dev generate-report --strict
```

Reports read run history through `MonitorRepository`, so nothing here is
sqlite specific and moving `results_db` to postgres needs no change.

## Report folders

One folder per report under `reports_path` (default `reports/`):

```
reports/
  default/
    report.yaml         # what to include
    template.html.j2    # how it looks
    static/             # optional - copied next to index.html
```

Output is written to `<reports_output_path>/<report-name>/index.html`, and
`output/` is git ignored.

## report.yaml

```yaml
title: "Nightly Jobs Overview"
subtitle: "Backups and overnight batch"

branding:
  organization: "Operations"

sections:              # or a list of the sections to show
  summary: true        # sections not named stay on
  successes: true
  run_times: true
  errors: true
  alerts: true

default_window: 30d    # window the page's sections cover, unless a range
                       # field asks for more

monitors:
  - match: "backup-*"  # monitor name or glob - a list works too
    tags: ["nightly"]  # matches the `tags` key of a monitor's [settings]
    exclude: "backup-old"
    fields:
      status:
        mode: latest
      run_time:
        mode: range
        last: 30d
      errors:
        mode: range
        since: 2026-07-01
```

A monitor is included when any entry matches it by name/glob or by tag, and
is dropped again by `exclude`. An entry with neither `match` nor `tags`
matches every monitor. When several entries match the same monitor their
fields are merged, later entries winning.

### Value modes

| Mode | Result |
| --- | --- |
| `latest` | The single most recent value, from before the report window if need be |
| `range` | Every value inside the window, oldest first, plus min/max/avg for numeric fields |

A `range` window is `last: 30d` (`s`, `m`, `h`, `d`, `w` units; a bare number
means days), or explicit `since:` / `until:` dates. Modes may be mixed freely
within one monitor - "current status: latest" alongside "run time trend:
range, last 30 days".

### Windows

The page's window - `window.since` / `window.until`, and the history the
summary, errors and alerts sections describe - is the widest window the report's
`range` fields ask for, widened to `default_window` when one is set. A field
with no `until` keeps the window open at that end, whatever order the fields
are declared in.

`latest` fields are resolved separately and never widen that window, so one
`latest` field does not drag the whole page back to the start of history. When
the window holds no value for a `latest` field, the monitor's most recent runs
are searched as well - `max_latest_runs` (50) bounds how far back that reaches.

`max_runs` (5000) caps the history loaded **per monitor**, so a busy monitor
cannot starve a quiet one. Hitting the cap is logged as a warning. Both limits
are constructor arguments of `ReportDataBuilder`.

### Field names

| Name | Value |
| --- | --- |
| `status` | Run status - `ok`, `alert`, `error`, `skipped`, `inactive` |
| `run_time` (or `duration`) | Run duration in seconds |
| `message` | Run message |
| `started_at`, `finished_at` | Run timestamps |
| `alert_count`, `report_count`, `error_count` | Per run counts |
| `errors` | One value per test error recorded |
| `alerts` | One value per alert raised |
| anything else | Matched against test keys and test types, globs allowed (e.g. `db.*`) |

## template.html.j2

Rendered by Jinja2 from the report's own folder, so `{% include %}` of
neighbouring files works. Autoescaping is on.

| Variable | Contents |
| --- | --- |
| `title`, `subtitle`, `branding`, `report_name` | Straight from report.yaml |
| `generated_at`, `window` | When the page was built, and the history it covers |
| `sections` | `sections.errors` etc - what to render |
| `monitors` | One entry per selected monitor (below) |
| `errors`, `alerts` | Flattened across all monitors, newest first |
| `summary` | `monitor_count`, `total_runs`, `success_count`, `failure_count`, `uptime_pct`, `error_count`, `alert_count`, `failing`, `avg_duration` |
| `extra` | Any other top level keys in report.yaml |

Each monitor entry holds `name`, `description`, `link`, `tags`, `fields`,
`stats`, `runs`, `errors`, `alerts`, `last_run_at`, `last_status`,
`next_run_at`, and `failing`. `stats` carries `total_runs`, `success_count`,
`failure_count`, `skipped_count`, `uptime_pct`, and min/max/avg duration.

A resolved field carries `mode`, `label`, `value`, `at`, `points`, and
`values`; `range` fields add `count`, `numbers`, `min`, `max`, and `avg`.
Every point is `{at, value, number, status, run_id, monitor, test_key,
test_type}`.

```jinja
{% for monitor in monitors %}
  <h3>{{ monitor.name }} - {{ monitor.fields.status.value }}</h3>
  <p>{{ monitor.stats.uptime_pct | percent }} uptime</p>
  {{ monitor.fields.run_time.numbers | sparkline }}
{% endfor %}
```

### Filters

| Filter | Result |
| --- | --- |
| `datetime` | `value \| datetime("%Y-%m-%d")` - blank for `None` |
| `duration` | Seconds as `1.25s` |
| `number` | Trims trailing zeros on whole numbers |
| `percent` | `99.5%`, or `-` when there is nothing to divide |
| `sparkline` | Inline svg trend line; needs two or more numbers |

Empty sections render their own empty state - a report over an empty database
produces a valid page rather than an error.

With `--all`, a report whose definition is broken is logged and skipped; the
remaining reports are still written and the command exits non-zero.
