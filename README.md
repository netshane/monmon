# monmon2

Runs a set of monitors, alerts, and reports.
A text file driven and extensible monitoring framework.

Monitors are toml files under
`monitors/`; the application loads them, decides what is due, runs the tests,
stores the results, and emails alerts and reports.

See [docs/monitors.md](docs/monitors.md) for the monitor file reference, and
[docs/reports.md](docs/reports.md) for the html report reference.

## Installation

```sh
uv sync
uv sync --group test

# Upgrade packages
uv lock --upgrade
uv lock --upgrade-package mtz-components
```

`ASPNETCORE_ENVIRONMENT` selects the settings environment (`Development` or
`Production`); `--dev` / `--prd` force it.

## Usage

```sh
# every monitor found under the monitors folder
uv run monmon.py --dev list

# the resolved settings of one monitor (templates already merged)
uv run monmon.py --dev show xyz

# configuration problems: monitors that failed to load, broken hierarchies,
# unknown test types
uv run monmon.py --dev validate

# database / docker / opensearch connections from settings, secrets redacted
uv run monmon.py --dev connections
uv run monmon.py --dev connections --json

# the contact lists of one monitor, and a test notification to one of them
uv run monmon.py --dev contacts xyz
uv run monmon.py --dev test-notify xyz alert

# next scheduled run for all monitors
uv run monmon.py --dev next-runs

# run monitors by name
uv run monmon.py --dev run xyz
uv run monmon.py --dev run xyz localhost --force

# work out what is due and run it - this is the scheduled entry point
uv run monmon.py --dev run-scheduled

# stored run history
uv run monmon.py --dev history --limit 10 --details

# delete run history, test results, and stale state older than N days
uv run monmon.py --dev purge --days 90
uv run monmon.py --dev purge --days 90 --monitor xyz

# delete all state, run history, and test results for one monitor
uv run monmon.py --dev purge-monitor xyz

# static html reports - reports/<name>/ -> output/<name>/index.html
uv run monmon.py --dev generate-report
uv run monmon.py --dev generate-report --template nightly --output /srv/www
uv run monmon.py --dev generate-report --all
```

`--simulate` (before the sub command) loads and schedules everything without
running tests, storing results, or sending notifications:

```sh
uv run monmon.py --dev --simulate run-scheduled
```

Run `run-scheduled` from cron / Task Scheduler as often as your shortest
`repeat` interval.

## Configuration

`settings.toml` holds connection strings and paths, `secrets.toml` holds
passwords and is excluded from source control.

| Setting | Purpose |
| --- | --- |
| `monitors_path` | Folder scanned recursively for monitor toml files |
| `custom_tests_path` | Folder holding modules used by `[test.custom.<module>]` |
| `monitor_template_names` | File names excluded from monitor loading (templates, sample files) |
| `reports_path` | Folder holding html report definitions, one sub folder per report |
| `reports_output_path` | Where `generate-report` writes pages (git ignored) |
| `results_db` | SQLAlchemy url for run history (sqlite today, postgres later) |
| `connections` | Named database and opensearch connections |
| `schedule.recheck_after_skipped`, `schedule.recheck_after_error`, `schedule.recheck_after_alert` | Re-run a monitor left in that status this long after its last run instead of on its normal cadence; `0` or absent = no recheck. Overridable per monitor in `[schedule]` |
| `schedule.recheck_max_attempts` | Rechecks allowed after the run that first failed, before the monitor goes back on its normal schedule; `0` or absent = unlimited |
| `contact.renotify_after_alert`, `contact.renotify_after_report`, `contact.renotify_after_error`, `contact.renotify_after_info`, `contact.renotify_after_notify` | Minimum gap between repeat notifications of that type for one monitor; `0` or absent = every run notifies. Overridable per monitor in `[contact]` |
| `email` | smtp host/port/from address; empty host logs emails instead of sending |
| `email.max_per_day` | Max recipient-sends in a rolling 24 hours; `0` or absent = unlimited |
| `email.max_per_recipient` | Max sends to one address within `cooldown_window_minutes`; `0` or absent = unlimited |
| `email.cooldown_window_minutes` | The per recipient window, default 60 |
| `email.send_log_retention_days` | How long `purge` keeps send log rows, default 7 |
| `slack`, `teams` | Bot token / incoming webhooks; unconfigured channels log messages instead of sending |
| `ping`, `http` | Timeouts for the ping and web tests |
| `docker` | Default docker `host` (the local engine unless set) and timeout for the docker tests |
| `contact.message` | Jinja2 notification templates, `[contact.message.<type>.<channel>]` |
| `contact.limits` | Length caps applied to rendered subjects and bodies |

### Notification templates

Every notification is rendered from a jinja2 template named
`[contact.message.<type>.<channel>]`, where `<type>` is one of `alert`,
`report`, `info`, `notify`, `error` and `<channel>` is `email`, `slack`, or
`teams`.  Each section may set `subject`, `body` (or `message`, the same field
under a name that reads better for chat), `html` for email, and `batch_subject`
for the subject of a combined notification.

Fields resolve one at a time: a monitor's own `[contact.message]` section wins
over `settings.toml`, which wins over the built in templates in
`locallib/message_templates.py`.  A monitor that overrides only `body` keeps the
`subject` it inherited.  `batch_subject` may only be set in `settings.toml`.

Templates get `name`, `description`, `link`, `tags`, `alert`, `reports`,
`test_results`, `started_at`, `duration_seconds`, `status`, `contact`,
`contact_type`, and `contact_channel`, plus the `table`, `html_table`, and
`datetime` filters.  `batch_subject` instead gets `monitor_count`,
`monitor_names`, `alert_count`, `report_count`, and the contact variables.
An undefined variable renders as `(Undefined)` rather than failing.

Templates are compiled when the monitor loads, so a syntax error is a monitor
load failure.  A template that fails at render time loses that one message and
is logged - the run and the other contacts carry on.  `test-notify` renders the
real template for the contact type and channel with placeholder values.

Run history lives in three tables created on first use: `monitor_runs`,
`monitor_test_results`, and `monitor_state` (last run time, last status, next
run time). Moving to postgres is a change to `results_db`, for example
`postgresql+psycopg://monitor:{passwd}@dbhost/monitors` with the password in
`secrets.toml` under `passwords.results_db`.

## Layout

| Module | Responsibility |
| --- | --- |
| `locallib/dependencies.py` | Service factory - build everything from here |
| `locallib/monitor_loader.py` | Loads toml files, resolves template chains |
| `locallib/monitor_models.py` | Monitor, schedule, hierarchy, contact, test config |
| `locallib/schedule_calculator.py` | Next run / due calculation |
| `locallib/monitor_tests/` | One class per test type |
| `locallib/monitor_test_factory.py` | Maps a test type onto its class |
| `locallib/monitor_runner.py` | Runs the tests of one monitor |
| `locallib/hierarchy_resolver.py` | Parent/child nodes and parent failures |
| `locallib/monitor_repository.py` | Run history and last run times |
| `locallib/contacts.py` | Parses and validates `<channel>:<target>` contact lists |
| `locallib/notifier.py` | Alert / report / info routing across contact lists |
| `locallib/message_templates.py` | Compiles and renders the `[contact.message]` templates |
| `locallib/email_sender.py` | smtp delivery (logs when no host is configured) |
| `locallib/email_quota.py` | Cross run send quota applied in front of smtp delivery |
| `locallib/chat_sender.py` | Slack and Teams delivery (logs when unconfigured) |
| `locallib/monitor_service.py` | Orchestration used by the CLI |
| `locallib/report_config_loader.py` | Finds and parses `reports/<name>/report.yaml` |
| `locallib/report_models.py` | Report config, monitor selectors, field windows |
| `locallib/report_data_builder.py` | Resolves a report config into template context |
| `locallib/report_renderer.py` | Jinja2 environment and presentation filters |
| `locallib/report_generator.py` | Renders reports and writes them to `output/` |

## Testing

```sh
# Run tests
uv run pytest tests/unit/

# Run single test
uv run pytest tests/unit/ -k "test_name"

# Run tests excluding slow tests
uv run pytest tests/unit/ -m "unit and not slow"
```

## Contributing

## License
