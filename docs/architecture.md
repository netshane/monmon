# Architecture

Orientation for working on monmon2. Read this **before** exploring the code -
it names the file to open for a given change so you do not have to search.

Companion docs: [monitors.md](monitors.md) (monitor toml reference),
[reports.md](reports.md) (html report reference), [../README.md](../README.md)
(cli usage and settings reference).

## What the application is

A cli batch application. There is no server and no long running process.
`run-scheduled` is invoked from cron / Task Scheduler, works out which monitors
are due, runs their tests, stores the results, and sends notifications.
`generate-report` renders static html from the stored history.

Everything a monitor does is declared in a toml file under `monitors/`; the
python code is the engine, not the configuration.

## Layers

```
monmon.py                     cli (click) - argument parsing and console output only
  |
  v
locallib/dependencies.py      settings + service factory - the only place that constructs
  |
  v
locallib/monitor_service.py   orchestration: load -> schedule -> run -> persist -> notify
  |         |          |          |
  |         |          |          +-> notifier.py -------> email_sender / chat_sender / file_sender
  |         |          +------------> monitor_repository.py -> sqlalchemy -> results_db
  |         +-----------------------> monitor_runner.py -> monitor_test_factory.py -> monitor_tests/*
  +---------------------------------> monitor_loader.py -> monitor_models.py
                                      hierarchy_resolver.py
                                      schedule_calculator.py

locallib/report_generator.py  separate pipeline, reads the same repository
  -> report_config_loader.py -> report_data_builder.py -> report_renderer.py -> output/
```

### Rules that hold everywhere

- **All modules live in `locallib/`.** No application code in the repo root
  except `monmon.py`.
- **`locallib/dependencies.py` is the only factory.** Every class takes its
  dependencies in the constructor and never constructs another class itself.
  A new class means a new `get_*()` function in `dependencies.py`.
- **`monmon.py` holds no logic.** It calls `_service(ctx)` (which is
  `dependencies.get_monitor_service`) or a `get_report_generator`, then formats
  output with `click.echo`.
- Settings are read *only* in `dependencies.py`, through `MonitorSettings`
  (`mtz_components.SettingsBase`, loaded from `settings.toml` + `secrets.toml`).
  No module below the factory reads settings or the environment.
- Logging is `loguru`'s `logger`, imported directly in whatever module needs it.

## Control flow: `run-scheduled`

`MonitorService.run_scheduled()` in `locallib/monitor_service.py` is the
backbone; most feature work touches one step of it.

1. `MonitorLoader.load_all()` walks `monitors_path` for `*.toml`, skips
   `monitor_template_names`, resolves each file's `settings.template` chain
   (recursive deep merge, cycle-detected), and builds a `MonitorDefinition`.
   Failures are collected into `loader.load_errors` rather than raised, and
   surfaced by `validate`.
2. `ScheduleCalculator.next_run(schedule, last_run, now, last_status, attempt_count)`
   decides the next run time per monitor; `due` means `next_run <= now`.
   Last-run state comes from the `monitor_state` table. A monitor left in
   `skipped` / `error` / `alert` comes off its normal cadence onto its
   `recheck_after_<status>` delay until it runs `ok` or `recheck_max_attempts`
   is used up - the attempt count is derived from `monitor_runs` by
   `MonitorRepository.consecutive_failure_counts()`, never stored.
3. Due monitors are ordered parents-first (`_order_by_hierarchy`) so a child
   sees its parent's fresh status.
4. Per monitor, `_run_definition`:
   - inactive -> `ResultStatus.INACTIVE`, nothing stored;
   - `HierarchyResolver.parent_failures()` -> `skip_on_parent_fail` produces a
     `SKIPPED` result, `alert_on_parent_fail` produces `forced_alerts`;
   - `MonitorRunner.run()` executes the tests;
   - `MonitorRepository.save_result()` persists;
   - `Notifier.notify()` routes messages (exceptions here are logged, never
     fatal).
5. `Notifier.flush()` sends anything held back by `combine_alerts` /
   `combine_reports`, then next-run times are re-stored.

`MonitorRunner.run()` registers the monitor's `[connections]` overrides on the
shared factories, runs each `TestConfig` through `MonitorTestFactory.create()`,
restores the connection snapshot in a `finally`, then rolls up the worst status
(`MonitorResult.rollup_status()`: ERROR > ALERT > all-SKIPPED > OK).

`--simulate` propagates from the cli into `dependencies` and swaps in logging
senders, skips test execution in the runner, and sets `persist_results=False`.

## Data model

`locallib/monitor_models.py` - configuration, all dataclasses with
`from_dict` classmethods:

| Class | Notes |
| --- | --- |
| `MonitorDefinition` | the fully resolved monitor; `raw` keeps the merged toml |
| `ScheduleConfig` | `cron` wins over `daily`/`repeat`/`days_of_week`; `start_time`/`end_time` always apply |
| `HierarchyConfig` | one per named hierarchy; a monitor may belong to several |
| `ContactConfig` | one `list[Contact]` per contact type; rejects the removed `*_email` keys |
| `TestConfig` | `test_type` is the `[test.*]` type, `instance` the name after it, `options` the rest |

`locallib/monitor_results.py` - runtime results:
`MonitorResult` -> `list[TestResult]` -> `list[Alert]` / `list[Report]`, with
`ResultStatus` in `ok | alert | error | skipped | inactive`.

Both files set `__test__ = False` on `TestConfig` / `TestResult` so pytest does
not try to collect them.

`locallib/monitor_repository.py` - five SQLAlchemy Core tables, created on
first use, deliberately free of sqlite-isms so `results_db` can become postgres:

- `monitor_runs` - one row per monitor run, with alert/report/error counts
- `monitor_test_results` - one row per test, plus the full `result_json`
- `monitor_state` - unique per monitor: `last_run_at`, `last_status`,
  `last_run_id`, `next_run_at`
- `email_send_log` - one row per recipient per delivered email, the state
  behind the cross run send quota; pruned by `purge` using
  `email.send_log_retention_days`
- `notification_state` - one row per monitor per contact type: when that type
  was last delivered and the monitor status at the time, the state behind the
  `renotify_after_<type>` delays in `locallib/notify_throttle.py`

## Extension recipes

### Add a monitor test type

1. Add the class to `locallib/monitor_tests/` (or extend an existing module -
   `db_tests.py`, `web_tests.py`, `opensearch_tests.py`, `ping_test.py`).
   Subclass `MonitorTest`, implement `execute(self, result)`; the base class
   handles timing and turns exceptions into `ResultStatus.ERROR`. Read options
   with `self.option()` / `self.required_option()` (these run the value through
   `ValueExpander`), and record findings with `self.add_alert()` /
   `self.add_report()`.
2. Export it from `locallib/monitor_tests/__init__.py`.
3. Register the toml section name in `MonitorTestFactory._builders()`, using
   the `_db` / `_web` / `_search` / `_db_free` helper that injects the right
   dependencies.  A type name is one segment (two for `custom.<module>`) - the
   loader treats anything after it in `[test.<type>.<instance>]` as the
   instance name and keeps it out of `TestConfig.test_type`.
4. Document the section in `docs/monitors.md`; add a case to
   `tests/unit/test_monitor_tests.py`.

No change to the runner, service, or cli is needed.

### Add a notification channel

1. Add the channel constant and parsing in `locallib/contacts.py`
   (`CHANNELS`, plus a `_parse_*` branch).
2. Add a sender class alongside `email_sender.py` / `chat_sender.py` /
   `file_sender.py`, with a `Logging*` variant used for `--simulate` and for
   when credentials are missing.
3. Build it in `dependencies.py` and pass it into `get_notifier()`.
4. Add the delivery branch in `Notifier._deliver()`.
5. Add default templates for the channel in
   `locallib/message_templates.py` (`default_templates()` covers every
   `<type>.<channel>` pair).

Repeat notifications are rate limited a layer above the senders:
`dependencies.get_notifier()` injects a `NotifyThrottleGate`
(`locallib/notify_throttle.py`) which the `Notifier` asks before rendering or
queueing anything, so a held back monitor never reaches a combined batch.  The
window is started only by a live, successful, unthrottled `Delivery`, and the
gate is left out entirely under `--simulate` so a simulated run cannot consume
one.

The email channel has one extra layer: `dependencies.get_email_sender()` wraps
the live `SmtpEmailSender` in `QuotaLimitedEmailSender`, which asks the
`EmailQuotaGate` in `locallib/email_quota.py` which recipients are still under
the `[email]` send limits.  Blocked recipients are dropped, not delayed, and
come back from `send()` in a `SendOutcome` so `Notifier._send_email` can mark
those `Delivery` rows `throttled`.  The gate is only wired in for a live,
non simulated sender - `--simulate` must never consume quota.

### Add a cli command

Add a `@cli.command` in `monmon.py`, get collaborators from `dependencies`, put
any real logic on `MonitorService` (or a new service class) and test that
instead of the command.

### Change how notifications read

Templates only. Resolution order per field is monitor `[contact.message]` ->
`settings.toml` -> `default_templates()` in `locallib/message_templates.py`.
Templates are compiled at monitor load time, so a syntax error is a load
failure; render failures lose one message and are logged.

### Add data to an html report

`report_data_builder.py` builds the template context (this is the big one at
~600 lines); `report_renderer.py` owns the jinja2 environment and presentation
filters (`datetime`, `duration`, `number`, `percent`, `sparkline`);
`report_models.py` holds the config/selector/field-window dataclasses;
`report_config_loader.py` reads `reports/<name>/report.yaml`. Report templates
and static assets live in `reports/<name>/`, output goes to `output/<name>/`.

## Supporting modules

| Module | Responsibility |
| --- | --- |
| `value_expander.py` | renders `{{ jinja }}` expressions in test option strings (`now`, `today`, `epoch`, ... context vars) |
| `json_extractor.py` | json path style extraction for web / opensearch tests |
| `db_connection_factory.py` | named db connections, snapshot/restore for per-monitor overrides; `results_db` is injected as a predefined connection in `dependencies.get_db_factory` |
| `connection_inspector.py` | read-only, secret-redacted view of `[connections]` behind the `connections` cli command |
| `opensearch_service.py` | opensearch clients, same snapshot/restore contract |
| `aws_service.py` | region/credentials for aws-signed opensearch |
| `http_client.py` | requests wrapper; `HttpClient.request()` is the entry point for the web tests (method, body, redirects); note `get_notification_http_client()` never disables tls |
| `ping_client.py` | icmp ping via the system tool |
| `custom_test_loader.py` | imports modules from `custom_tests_path` for `[test.custom.<module>]` |
| `helpers.py` | small shared utilities, including `parse_interval` for the `"5 min"` style delays |
| `notify_throttle.py` | the `renotify_after_<type>` rate limit on repeat notifications |

## Conventions

- `uv run python` / `uv run pytest`; never `pip` or `venv` directly.
- `uvx ruff format` then `uvx ruff check --fix` before finishing.
- Tests are pure unit tests in `tests/unit/`, one file per module, constructing
  classes directly with fakes for their constructor dependencies - they never
  call `dependencies.get_*()`. Markers: `unit`, `slow`, `unit_external`,
  `integration`.
- `docs/features/` holds feature specifications; per `CLAUDE.md`, append an
  implementation summary (with the session id) to the spec file when done.
  Completed specs move to `docs/features/archive/`.
- Commit messages: `<type>: <message>` plus bullets.

## Where not to look

- `monitors.db`, `output/`, and `.venv/` are generated; never edit them.
- `monitors/sample.toml` and `monitors/base.toml` are documentation/templates
  excluded from loading via `monitor_template_names`.
