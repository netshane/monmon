# Feature Request: Static HTML Report Generator

## Summary
Add a CLI command that renders monitor results into static HTML report(s), driven by user-defined YAML + Jinja2 templates.

## Data source
Reads from the project's existing SQLite database (current storage layer). Data access should go through the existing data/query layer rather than raw SQL where possible, so this stays compatible if/when the project migrates to Postgres.

## Command

```sh
monitor generate-report [--template <name>] [--output <dir>] [--all]
--template — name of a report definition in reports/ (default: default)
--output — override output directory (default: output/)
--all — render every template found in reports/ in one run
```

## Report templates

Location: reports/<name>/
Format: YAML config (report.yaml) + Jinja2 template (template.html.j2) per report folder
report.yaml specifies, per monitor/field entry:
Which monitor(s) to include (by ID, tag, or glob pattern)
Value mode — either:
latest — return only the most recent value for that field
range — return a list of values over a timeframe, with its own since/until or relative window (e.g. last: 7d) defined per entry
Which sections to render: successes, run times, errors, alerts, summary stats
Page title/branding (optional)
This means different fields in the same report can mix modes — e.g. "current status: latest" alongside "run time trend: range, last 30 days"
template.html.j2 receives the resolved data (already reduced to latest value or list, per the config) as context and controls layout/presentation
Multiple templates supported; each lives in its own subfolder under reports/

Example report.yaml snippet

```yaml
title: "Nightly Jobs Overview"
monitors:
  - match: "backup-*"
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

## Output

Written to output/<template-name>/index.html
output/ added to .gitignore
Self-contained: inline or locally-bundled CSS/JS, no external CDN dependency, so pages work fully offline via file://

## Displayed data

Success/failure counts per monitor
Run time trends (min/max/avg, optional simple sparkline/chart)
Error details (message, timestamp, monitor)
Active/recent alerts
Optional: overall health summary at top (uptime %, monitors currently failing)
Optional: filterable/sortable table via lightweight embedded JS (no framework dependency)

## Non-goals

No live/auto-refreshing dashboard (static output only)
No authentication/access control on generated pages
No Postgres-specific code — abstraction should just avoid precluding the future migration, not implement it now

## Acceptance criteria

 - Running the command with no args produces output/default/index.html using reports/default/report.yaml + template.html.j2
 - mode: latest returns a single value; mode: range returns a list scoped to the specified window
 - Multiple templates can be rendered independently (--template <name>) or all at once (--all)
 - Generated page has no broken references when opened locally via file://
 - output/ is git-ignored by default
 - Errors/alerts sections render correctly when empty (no crash, clean empty state)
 - Data queries go through the existing data-access layer, not raw SQLite-specific SQL, to keep the door open for Postgres

## Implementation summary

Implemented 2026-08-12 (Claude Code session `6a64eff9-0077-486e-a249-51b2623a86cb`).

**Command** (`monmon.py:generate-report`)

```sh
uv run monmon.py --dev generate-report [--template <name>] [--output <dir>] [--all] [--strict]
```

`--strict` is an extra beyond the request: it turns unknown template variables
into an error instead of a blank.

**New modules** (all wired through `locallib/dependencies.py`, constructor-injected):

- `report_models.py` - `ReportConfig` / `MonitorSelector` / `FieldSpec`, duration + timestamp parsing, window resolution
- `report_config_loader.py` - discovers and parses `reports/<name>/report.yaml`
- `report_data_builder.py` - resolves config into template context, reading only through `MonitorRepository`
- `report_renderer.py` - Jinja2 env plus `datetime`/`duration`/`number`/`percent`/`sparkline` filters
- `report_generator.py` - renders and writes `output/<name>/index.html`, copying an optional `static/` folder alongside

**Data access:** added `monitor_names` / `since` / `until` / unbounded-`limit`
options to `get_runs`, a multi-run `get_test_results`, and `get_monitor_names` -
all plain SQLAlchemy Core, no SQLite-specific SQL. History is fetched in one
runs query plus chunked test-result queries per report.

**Supporting changes:**

- Monitors now accept `tags` in `[settings]` (list or delimited string) - the feature asked for tag matching and nothing carried tags before. Documented in `docs/monitors.md` and `monitors/sample.toml`.
- `reports_path` / `reports_output_path` settings; `output/` was already git-ignored.
- `reports/default/` ships an annotated `report.yaml` and a self-contained template: inlined CSS/JS, light/dark, SVG sparklines, sortable + filterable tables, empty states per section.
- Added `jinja2` and `pyyaml`; new `docs/reports.md` reference and README updates.

**Verification:** 191 tests pass (49 new across models, config loader, data
builder, generator, and the CLI command), ruff format/check clean. The real
`reports/default` template was also rendered against a 3-monitor x 20-run
synthetic database and driven in a browser: sparklines, sort (uptime sorted
70/75/95%), and filter all work, the only console entry was the server's own
`favicon.ico` 404, and the page's sole external reference is a monitor's own
`link` from its definition - no external asset requests.

**Note on window semantics:** the summary/errors/alerts sections cover the
widest window any field requests (falling back to `default_window`), so a
report containing any `latest` field loads unbounded history. This is
documented in `docs/reports.md`; the cap knob is `ReportDataBuilder(max_runs=...)`.
