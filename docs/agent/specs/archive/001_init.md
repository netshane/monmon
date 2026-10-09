Build an application that runs a set of monitors, alerts, and reports.
The monitors should be defined in the folder "monitors".  All toml files in this folder and sub-folders should be loaded as monitor definitions.
The application should allow monitors to be run by name.
The application should have a command determine the next scheduled run for all monitors and run all monitors currently scheduled to run
The application should store the last run time of each monitor and the results of each monitor in a database.  Use sqlite for now, but be prepared to replace it with postgres.

Below is a sample monitor file with comments.  

```
[settings]
name = "xyz"
description = "This monitors the xyz"
link = "https://example.com/externaldocs/support.md"

template = "base.toml" # Use the settings from this template.  Settings in this file override template settings
                       # templates should be able to chain.
                       # this should allow paths that are relative to the monitors folder, or absolute paths

monitor_type_alert = true
monitor_type_report = false

active = true # whether this should run or not

[connections]
# connections defined here should be added to the list of connections available
# from the settings file
aws_dev = ""
es_prd = {host = "sample.example.com", default_index = "index.example.com*"}

[schedule]
cron = "xxxxx" # takes a crontab formatted schedule, if specified this overrides other settings besides start_time and end_time
daily = true
days_of_week = "S M Tu W Th F Sa" # Specify a space delimited string of the days of week to run monitor on
repeat = "5 min" # time period to rerun the task
start_time = ""  # time of day to start running the task
end_time = ""    # time of day to stop running the task

[hierarchy]
# creates a parent child chain
# should generate a warning if the hierarchy is broken
# this is an optional section
node_name = "server 123"
parent_node = "server group 1"
skip_on_parent_fail = true    # don't run this monitor if the parent has failed
alert_on_parent_fail = false  # place monitor in alert state if parent has failed

[hierarchy.A]
# alternate hierarchy "A"
# should be handled the same as the primary hierarchy just separately
node_name = "app 123"
parent_node = "app group 1"
skip_on_parent_fail = true    # don't run this monitor if the parent has failed
alert_on_parent_fail = false  # place monitor in alert state if parent has failed

[hierarchy.B]
# alternate hierarchy "B"
# should be handled the same as the primary hierarchy just separately
node_name = "app 123"
parent_node = "nework aws"
skip_on_parent_fail = true    # don't run this monitor if the parent has failed
alert_on_parent_fail = false  # place monitor in alert state if parent has failed

[contact]
alert_email = ""   # If the monitor generates an alert, send the message to this email
error_email = ""   # If any errors occur running the notifier, send it to this email
info_email = ""    # Send all outcomes and info messages from this monitor to this email  

notify_email = ""  # Send notification that the monitor has run if this email is not empty

report_email = ""  # results of any report monitor will be sent to this email

combine_report_emails = true # if true, combine the reports into a single email
combine_alert_emails = true # if true, combine all alerts into a single email before sending, otherwise send each alert email 
                            # as it arises

[test.ping]
# ping the server or servers listed and generate an alert if they cannot be reached
key = "network.appgroup.app.ping"  # a unique identifier for the monitor used to store results
server = "example.com"
servers = [
"123.123.123.123",
"foo.com",
]

[test.dbflag]
# Test consists of a query that returns a scalar
# The test generates an alert if the scalar is greater than 0, the alert will return the scalar value
# connection comes from global config
key = "network.appgroup.app.dbflag"  # a unique identifier for the monitor used to store results
connection = "db_aws" # this should create a connection using the depencies method get_db
query =  """ 
SELECT 
    COUNT(*) 
FROM Monitors AS a
WHERE a.Monitor = 'SomeMonitor' 
  AND a.Status = 'Failed'
  AND a.ScanDate > DATEADD(day, -1, GETDATE())
"""

[test.dbthreshold]
# Test consists of a query that returns at least a column called Name, and a column called Value
# The monitor will generate an alert for any row where the Value column is greater than the specified threshold
# The alert will return the Name column, the Value, the threshold, and anything in a column called Details if present
key = "network.appgroup.app.moncount"  # a unique identifier for the monitor used to store results
connection = "db_aws" # this should create a connection using the depencies method get_db
threshold = 4.2
query =  """ 
SELECT 
	a.Monitor AS [Name],
	COUNT(*) AS [Value],
	'Some descripption' AS [Details]
FROM Monitors AS a
GROUP BY a.Montitor	
"""

[test.dbreport]
# Monitor generates a report that consists of a table of the columns returned
# 
key = "network.appgroup.app.metric"  # a unique identifier for the monitor used to store results
connection = "db_aws" # this should create a connection using the depencies method get_db
query = """
SELECT 
	a.Monitor AS [Name],
    a.ScanDate AS [ScanDate],
    a.Status AS [Status]
FROM Monitors AS a
WHERE a.Monitor = 'SomeMonitor' 
  AND a.ScanDate > DATEADD(day, -1, GETDATE())
"""

[test.opensearch_flag]
# Monitor will run the elastic search query in the query setting and will extract the first value
# specified by jq.  If this value exists and is greater than 0, it will generate an alert
# values delimited by {{> <}} should be evaluated
key = "network.appgroup.app.metric"  # a unique identifier for the monitor used to store results
connection = "es_prd" # this should create a connection using the dependencies method get_opensearch_service
jq = ".properties.count"
query = """
{
	"sort": [
		{
			"Timestamp": {
				"order": "desc",
				"unmapped_type": "boolean"
			}
		}
	],
	"query": {
		"bool": {
			"must": [],
			"filter": [
				{
					"match_all": {}
				},
				{
					"match_phrase": {
						"Level": "Information"
					}
				},
				{
					"match_phrase": {
						"Properties.Application": "SomeApplication"
					}
				},
				{
					"range": {
						"Timestamp": {
							"gte": "{{>today_iso_format<}}",
							"lte": "{{>yesterday_io_format<}}",
							"format": "strict_date_optional_time"
						}
					}
				}
			],
			"should": [],
			"must_not": []
		}
	}
 }
"""
```

## Implementation summary

Implemented 2026-08-12 in Claude Code session `443e3ca8-0fce-432b-9f4e-b30e3ecf2b68`.

Everything is wired through `locallib/dependencies.py` with constructor
injection, and 137 unit tests pass (`uvx ruff check` clean).

**Loading** — `MonitorLoader` walks `monitors/` recursively for all `*.toml`.
`template` chains (deep merge, child wins), resolved relative to the monitors
folder, relative to the referencing file, or absolute; cycles and missing
templates are reported. `[test.custom.x]` sections are flattened;
`[hierarchy]` / `[hierarchy.A]` become independent hierarchies. Files in
`monitor_template_names` (default `base.toml`) are templates only.

**Scheduling** — `ScheduleCalculator`: `cron` (croniter) overrides
`daily`/`repeat`, with `start_time`/`end_time` still applied;
`repeat = "5 min"` counts from the stored last run; `days_of_week` pushes runs
to the next allowed day. No schedule keys at all = manual-only.

**Running** — `MonitorService` orchestrates: computes next runs for all
monitors, runs everything due (parents before children), applies
`skip_on_parent_fail` / `alert_on_parent_fail`, persists, and notifies.
`MonitorRunner` registers a monitor's `[connections]` onto the shared factories
before its tests run.

**Tests implemented** — `ping`, `dbflag`, `dbthreshold`, `dbreport`,
`opensearch_flag`, `elasticsearch_report`, `html_200`, `html_json_exists`,
`html_json_value`, `custom.<module>` (from `custom_tests/`). `{{>token<}}`
expansion and jq-subset path extraction (`.a.b`, `[0]`, `[]`, quoted keys) are
separate injectable classes — a path resolver was written rather than adding a
compiled `jq` dependency; it covers the forms in the sample file.

**Storage** — `MonitorRepository` on plain SQLAlchemy Core: `monitor_runs`,
`monitor_test_results`, `monitor_state` (last run time, last status, next run).
Switching to postgres is only the `results_db` url in `settings.toml`.

**CLI** — `list`, `show`, `validate`, `next-runs`, `run <names…> [--force]`,
`run-scheduled`, `history`, `purge`, plus global `--simulate` (no test
execution, no persistence, emails logged).

Two things to flag: with no smtp `host` in `settings.toml`, `Notifier` logs
emails instead of sending them (`SmtpEmailSender` takes over once configured).
And `validate` exits 1 against the shipped `monitors/sample.toml` — its
`cron = "xxxxx"` and doc-only parent nodes are genuinely invalid; that file was
left untouched as documentation and a working example was added at
`monitors/examples/localhost.toml`. Reference docs are in `docs/monitors.md`.
