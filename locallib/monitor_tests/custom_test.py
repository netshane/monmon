"""Runs a command from a module in the custom_tests folder."""

from __future__ import annotations

from ..custom_test_loader import CustomTestLoader
from ..monitor_models import TestConfig
from ..monitor_results import Alert, Report, ResultStatus, TestResult
from ..value_expander import ValueExpander
from .monitor_test import MonitorTest


class CustomTest(MonitorTest):
    """Calls `command` in the module named by the `[test.custom.<module>]` section.

    The command is called with the expanded `args` and may return:

    * a dict - treated as an alert (`message`, `name`, `value`, `details` keys
      are recognised; anything else is rendered into the details)
    * a list - treated as report rows (a list of dicts uses the dict keys as
      columns; a list of lists uses the `columns` option or positional names)
    * a `TestResult` - used as is
    * None - the test passes with no alert
    """

    test_type = "custom"

    def __init__(
        self,
        config: TestConfig,
        value_expander: ValueExpander,
        custom_test_loader: CustomTestLoader,
        module_name: str,
    ):
        super().__init__(config, value_expander)
        self.custom_test_loader = custom_test_loader
        self.module_name = module_name

    def execute(self, result: TestResult):
        command_name = str(self.required_option("command"))
        args = self.option("args") or []
        if isinstance(args, str):
            args = [args]

        command = self.custom_test_loader.get_command(self.module_name, command_name)
        returned = command(*args)

        self._apply(result, returned)

    def _apply(self, result: TestResult, returned):
        if returned is None:
            result.message = "Custom test returned no alert"
            return

        if isinstance(returned, TestResult):
            result.status = returned.status
            result.message = returned.message
            result.value = returned.value
            result.alerts = returned.alerts
            result.reports = returned.reports
            result.error = returned.error
            return

        if isinstance(returned, Alert):
            self.add_alert(result, returned)
            return

        if isinstance(returned, Report):
            self.add_report(result, returned)
            return

        if isinstance(returned, dict):
            if not returned:
                result.message = "Custom test returned no alert"
                return
            self.add_alert(result, self._alert_from_dict(returned))
            return

        if isinstance(returned, (list, tuple)):
            self.add_report(result, self._report_from_rows(list(returned)))
            result.value = len(returned)
            return

        result.status = ResultStatus.ERROR
        result.error = (
            f"Custom test '{self.module_name}.{result.test_type}' returned an "
            f"unsupported type: {type(returned).__name__}"
        )
        result.message = result.error

    def _alert_from_dict(self, data: dict) -> Alert:
        known = ("message", "name", "value", "threshold", "details")
        extra = {k: v for k, v in data.items() if k not in known}
        details = data.get("details")
        if extra:
            rendered = ", ".join(f"{k}={v}" for k, v in extra.items())
            details = f"{details}; {rendered}" if details else rendered

        return Alert(
            message=str(
                data.get("message") or f"Custom test {self.key} raised an alert"
            ),
            name=data.get("name") or self.key,
            value=data.get("value"),
            threshold=data.get("threshold"),
            details=details,
        )

    def _report_from_rows(self, rows: list) -> Report:
        if rows and isinstance(rows[0], dict):
            columns: list[str] = []
            for row in rows:
                for column in row.keys():
                    if column not in columns:
                        columns.append(str(column))

            return Report(
                title=self.key,
                columns=columns,
                rows=[[row.get(c) for c in columns] for row in rows],
            )

        configured = self.option("columns") or []
        table_rows = [
            list(row) if isinstance(row, (list, tuple)) else [row] for row in rows
        ]
        width = max((len(r) for r in table_rows), default=0)
        columns = [str(c) for c in configured] or [
            f"column{i + 1}" for i in range(width)
        ]

        return Report(title=self.key, columns=columns, rows=table_rows)
