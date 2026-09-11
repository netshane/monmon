import json

from loguru import logger
import click

from locallib.dependencies import (
    console_logging,
    force_environment,
    get_connection_inspector,
    get_email_send_log_retention_days,
    get_monitor_service,
    get_report_generator,
    set_settings_file,
    set_settings_extenstion_file,
)
from locallib.contacts import CONTACT_TYPES
from locallib.helpers import format_table
from locallib.monitor_results import MonitorResult


@click.group()
@click.option("--dev/--no-dev", default=False)
@click.option("--prd/--no-prd", default=False)
@click.option(
    "--simulate",
    is_flag=True,
    default=False,
    help="Load and schedule monitors without running tests, storing results, or sending email",
)
@click.option(
    "--settings",
    default=None,
    type=str,
    help="Use specified file as primary settings file instead of settings.toml",
)
@click.option(
    "--extend",
    default=None,
    type=str,
    help="Override primary settings with the extension file.  Should be a toml file with the settings.toml format.",
)
@click.pass_context
def cli(
    ctx,
    dev: bool,
    prd: bool,
    simulate: bool,
    settings: str | None,
    extend: str | None,
):
    force_environment(dev, prd)
    if settings:
        set_settings_file(settings)
    if extend:
        set_settings_extenstion_file(extend)
    ctx.ensure_object(dict)

    console_logging()

    ctx.obj["simulate"] = simulate


def _service(ctx):
    return get_monitor_service(simulate=ctx.obj.get("simulate", False))


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


@cli.command("list")
@click.pass_context
def list_monitors(ctx):
    """List every monitor definition found under the monitors folder."""
    service = _service(ctx)
    monitors = service.monitors()

    rows = []
    for name, definition in sorted(monitors.items()):
        types = []
        if definition.monitor_type_alert:
            types.append("alert")
        if definition.monitor_type_report:
            types.append("report")

        rows.append(
            [
                name,
                "yes" if definition.active else "no",
                "/".join(types) or "-",
                len(definition.tests),
                definition.schedule.cron
                or definition.schedule.repeat
                or ("daily" if definition.schedule.daily else "-"),
                definition.source_file,
            ]
        )

    click.echo(
        format_table(["monitor", "active", "type", "tests", "schedule", "file"], rows)
    )
    click.echo(f"\n{len(rows)} monitor(s)")


@cli.command("connections")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def list_connections(as_json: bool):
    """List the database, docker, and opensearch connections from settings."""
    infos = get_connection_inspector().list()

    if as_json:
        click.echo(
            json.dumps(
                [
                    {"name": i.name, "kind": i.kind, "connection": i.connection}
                    for i in infos
                ],
                indent=2,
            )
        )
        return

    click.echo(
        format_table(
            ["name", "kind", "connection"],
            [[i.name, i.kind, i.connection] for i in infos],
        )
    )
    click.echo(f"\n{len(infos)} connection(s)")


@cli.command("show")
@click.argument("name")
@click.pass_context
def show_monitor(ctx, name: str):
    """Show the resolved settings of a single monitor."""
    service = _service(ctx)
    definition = service.get_monitor(name)

    click.echo(f"name:        {definition.name}")
    click.echo(f"description: {definition.description or ''}")
    click.echo(f"link:        {definition.link or ''}")
    click.echo(f"file:        {definition.source_file}")
    click.echo(f"template:    {definition.template or ''}")
    click.echo(f"active:      {definition.active}")
    click.echo(
        f"types:       alert={definition.monitor_type_alert} "
        f"report={definition.monitor_type_report}"
    )
    click.echo(f"schedule:    {definition.schedule}")

    for hierarchy_name, hierarchy in definition.hierarchies.items():
        click.echo(
            f"hierarchy {hierarchy_name}: {hierarchy.node_name} -> "
            f"{hierarchy.parent_node} "
            f"(skip={hierarchy.skip_on_parent_fail}, alert={hierarchy.alert_on_parent_fail})"
        )

    click.echo("\ntests:")
    for test in definition.tests:
        click.echo(f"  {test.test_type}: {test.key}")


@cli.command("contacts")
@click.argument("name")
@click.pass_context
def contacts(ctx, name: str):
    """Show the contact lists of a single monitor."""
    service = _service(ctx)
    definition = service.get_monitor(name)

    rows = [
        [contact_type, contact.channel, contact.address]
        for contact_type in CONTACT_TYPES
        for contact in definition.contact.for_type(contact_type)
    ]

    click.echo(format_table(["type", "channel", "target"], rows))
    click.echo(
        f"\ncombine_alerts={definition.contact.combine_alerts} "
        f"combine_reports={definition.contact.combine_reports}"
    )


@cli.command("test-notify")
@click.argument("name")
@click.argument("contact_type", type=click.Choice(CONTACT_TYPES))
@click.pass_context
def test_notify(ctx, name: str, contact_type: str):
    """Send a test notification to one of a monitor's contact lists."""
    service = _service(ctx)
    definition = service.get_monitor(name)

    deliveries = service.notifier.send_test(definition, contact_type)
    if not deliveries:
        click.echo(
            f"Monitor '{definition.name}' has no '{contact_type}' contacts configured"
        )
        raise SystemExit(1)

    click.echo(
        format_table(
            ["channel", "target", "result"],
            [[d.contact.channel, d.contact.address, d.status] for d in deliveries],
        )
    )

    failed = [d for d in deliveries if not d.sent]
    throttled = [d for d in deliveries if d.throttled]
    logged = [d for d in deliveries if d.sent and not d.live and not d.throttled]

    click.echo(
        f"\n{len(deliveries) - len(failed) - len(throttled)} of "
        f"{len(deliveries)} delivered"
    )
    if throttled:
        click.echo(
            f"{len(throttled)} contact(s) were dropped by the email send quota - "
            f"see [email] max_per_day / max_per_recipient in settings.toml"
        )
    if logged:
        click.echo(
            f"{len(logged)} contact(s) were logged only - that channel has no "
            f"credentials in settings.toml, or --simulate is set"
        )

    if failed:
        click.echo(f"{len(failed)} contact(s) failed - see the log")
        raise SystemExit(1)


@cli.command("validate")
@click.pass_context
def validate(ctx):
    """Load every monitor and report configuration problems."""
    service = _service(ctx)
    monitors = service.monitors()
    warnings = service.validate()

    unknown_tests = []
    factory = service.runner.test_factory
    for definition in monitors.values():
        for test_config in definition.tests:
            try:
                factory.create(test_config)
            except Exception as e:
                unknown_tests.append(f"{definition.name}: {e}")

    for warning in warnings + unknown_tests:
        click.echo(f"WARNING: {warning}")

    click.echo(
        f"\n{len(monitors)} monitor(s) loaded, "
        f"{len(warnings) + len(unknown_tests)} warning(s)"
    )

    if warnings or unknown_tests:
        raise SystemExit(1)


@cli.command("next-runs")
@click.option(
    "--store/--no-store", default=True, help="Persist the calculated next run times"
)
@click.pass_context
def next_runs(ctx, store: bool):
    """Show the next scheduled run for all monitors."""
    service = _service(ctx)
    entries = service.schedule_entries()

    if store and not ctx.obj.get("simulate", False):
        service.store_next_runs(entries)

    rows = [
        [
            entry.name,
            "yes" if entry.active else "no",
            _format_time(entry.last_run_at),
            entry.last_status or "",
            _format_time(entry.next_run_at) or ("manual" if entry.active else ""),
            "DUE" if entry.due else "",
        ]
        for entry in entries
    ]

    click.echo(
        format_table(
            ["monitor", "active", "last run", "last status", "next run", "due"], rows
        )
    )
    click.echo(f"\n{sum(1 for e in entries if e.due)} monitor(s) due now")


@cli.command("run")
@click.argument("names", nargs=-1, required=True)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Run even when the monitor is inactive or its parent has failed",
)
@click.pass_context
def run(ctx, names: tuple[str, ...], force: bool):
    """Run one or more monitors by name."""
    service = _service(ctx)

    results = service.run_monitors(list(names), force=force)
    _echo_results(results)

    if any(r.status.is_failure for r in results):
        raise SystemExit(1)


@cli.command("run-scheduled")
@click.pass_context
def run_scheduled(ctx):
    """Determine the next scheduled run for all monitors and run everything due."""
    service = _service(ctx)

    results = service.run_scheduled()
    _echo_results(results)


@cli.command("history")
@click.option("--monitor", default=None, help="Limit history to one monitor")
@click.option("--limit", default=20, show_default=True)
@click.option("--details", is_flag=True, default=False, help="Include per-test results")
@click.pass_context
def history(ctx, monitor: str | None, limit: int, details: bool):
    """Show stored monitor run history."""
    service = _service(ctx)
    runs = service.repository.get_runs(monitor_name=monitor, limit=limit)

    rows = [
        [
            r["id"],
            r["monitor_name"],
            r["status"],
            _format_time(r["started_at"]),
            f"{(r['duration_seconds'] or 0):.2f}s",
            r["alert_count"],
            r["report_count"],
            r["error_count"],
            r["message"] or "",
        ]
        for r in runs
    ]

    click.echo(
        format_table(
            [
                "id",
                "monitor",
                "status",
                "started",
                "duration",
                "alerts",
                "reports",
                "errors",
                "message",
            ],
            rows,
        )
    )

    if not details:
        return

    for r in runs:
        click.echo(f"\nrun {r['id']} - {r['monitor_name']}")
        test_rows = [
            [
                t["test_key"],
                t["test_type"],
                t["status"],
                t["value"] or "",
                t["message"] or "",
            ]
            for t in service.repository.get_test_results(r["id"])
        ]
        click.echo(
            format_table(["key", "type", "status", "value", "message"], test_rows)
        )


@cli.command("purge")
@click.option(
    "--days", default=90, show_default=True, help="Delete run history older than this"
)
@click.option("--monitor", default=None, help="Limit the purge to a single monitor")
@click.pass_context
def purge(ctx, days: int, monitor: str | None):
    """Delete old run history, test results, and stale state."""
    from datetime import datetime, timedelta

    service = _service(ctx)
    cutoff = datetime.now() - timedelta(days=days)
    deleted = service.repository.purge_runs_before(cutoff, monitor_name=monitor)

    scope = f" for monitor '{monitor}'" if monitor else ""
    click.echo(f"Deleted {deleted} run(s){scope} started before {_format_time(cutoff)}")

    # the email send log is deployment wide, so a single monitor purge leaves
    # it alone; its retention comes from [email] rather than --days
    if not monitor:
        log_cutoff = datetime.now() - timedelta(
            days=get_email_send_log_retention_days()
        )
        log_deleted = service.repository.purge_email_send_log_before(log_cutoff)
        click.echo(
            f"Deleted {log_deleted} email send log row(s) sent before "
            f"{_format_time(log_cutoff)}"
        )


@cli.command("purge-monitor")
@click.argument("name")
@click.pass_context
def purge_monitor(ctx, name: str):
    """Delete all state, run history, and test results for a single monitor."""
    service = _service(ctx)
    deleted = service.repository.purge_monitor(name)

    click.echo(f"Deleted {deleted} run(s) and all state for monitor '{name}'")


@cli.command("generate-report")
@click.option(
    "--template",
    default="default",
    show_default=True,
    help="Name of a report definition folder under reports/",
)
@click.option("--output", default=None, help="Override the output directory")
@click.option(
    "--all",
    "render_all",
    is_flag=True,
    default=False,
    help="Render every report found in reports/",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Fail the render on undefined template variables",
)
def generate_report(template: str, output: str | None, render_all: bool, strict: bool):
    """Render monitor history into static html report(s)."""
    generator = get_report_generator(output_path=output, strict=strict)
    failed = 0

    if render_all:
        found = generator.report_names()
        generated = generator.generate_all()
        failed = len(found) - len(generated)
    else:
        generated = [generator.generate(template)]

    if not generated:
        click.echo("No reports were generated")
        raise SystemExit(1)

    rows = [[r.name, r.title, r.monitor_count, r.output_path] for r in generated]
    click.echo(format_table(["report", "title", "monitors", "output"], rows))
    click.echo(f"\n{len(generated)} report(s) generated")

    if failed:
        click.echo(f"{failed} report(s) failed - see the log")
        raise SystemExit(1)


def _echo_results(results: list[MonitorResult]):
    rows = [
        [
            result.monitor_name,
            result.status.value,
            f"{result.duration_seconds:.2f}s",
            len(result.alerts),
            len(result.reports),
            result.message or "",
        ]
        for result in results
    ]

    click.echo(
        format_table(
            ["monitor", "status", "duration", "alerts", "reports", "message"], rows
        )
    )

    for result in results:
        for alert in result.alerts:
            click.echo(f"ALERT {result.monitor_name}: {alert.message}")
        for report in result.reports:
            click.echo(f"\nREPORT {result.monitor_name} - {report.title}")
            click.echo(format_table(report.columns, report.rows, max_rows=25))


if __name__ == "__main__":
    try:
        # console_logging()
        # standard_logging()
        cli(obj={})
    except Exception as e:
        logger.exception(f"Uncaught exception: {e}", exception=e)
