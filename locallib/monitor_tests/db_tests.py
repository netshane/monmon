"""Database backed monitor tests."""

from __future__ import annotations

from sqlalchemy import text

from ..db_connection_factory import DbConnectionFactory
from ..helpers import to_number
from ..monitor_models import TestConfig
from ..monitor_results import Alert, Report, ResultStatus, TestResult
from ..value_expander import ValueExpander
from .monitor_test import MonitorTest


class DbTestBase(MonitorTest):
    """Shared query execution for the database tests."""

    def __init__(
        self,
        config: TestConfig,
        value_expander: ValueExpander,
        db_factory: DbConnectionFactory,
    ):
        super().__init__(config, value_expander)
        self.db_factory = db_factory

    def run_query(
        self, query: str | None = None, params: dict | None = None
    ) -> tuple[list[str], list[list]]:
        connection = self.required_option("connection")
        if query is None:
            query = str(self.required_option("query"))

        engine = self.db_factory.create(str(connection))
        with engine.connect() as conn:
            cursor = conn.execute(text(query), params or {})
            columns = list(cursor.keys())
            rows = [list(row) for row in cursor.fetchall()]

        return columns, rows

    def scalar(self):
        _columns, rows = self.run_query()
        if not rows or not rows[0]:
            return None

        return rows[0][0]

    @staticmethod
    def column_index(columns: list[str], name: str) -> int | None:
        lowered = [str(c).lower() for c in columns]
        target = name.lower()
        if target in lowered:
            return lowered.index(target)

        return None


class DbFlagTest(DbTestBase):
    """Alerts when a scalar query returns a value greater than zero."""

    test_type = "dbflag"

    def execute(self, result: TestResult):
        value = self.scalar()
        result.value = value

        numeric = to_number(value)
        if numeric is None:
            result.status = ResultStatus.ERROR
            result.error = f"Query did not return a numeric scalar (got {value!r})"
            result.message = result.error
            return

        if numeric > 0:
            self.add_alert(
                result,
                Alert(
                    name=self.key,
                    message=f"Flag query returned {numeric}",
                    value=numeric,
                ),
            )
            return

        result.message = f"Flag query returned {numeric}"


class DbThresholdTest(DbTestBase):
    """Alerts for each row whose Value column exceeds the threshold."""

    test_type = "dbthreshold"

    def execute(self, result: TestResult):
        threshold = to_number(self.required_option("threshold"))
        if threshold is None:
            raise ValueError(f"Test '{self.key}' has a non-numeric threshold")

        columns, rows = self.run_query()

        name_index = self.column_index(columns, "Name")
        value_index = self.column_index(columns, "Value")
        details_index = self.column_index(columns, "Details")

        if value_index is None:
            raise ValueError(
                f"Test '{self.key}' (dbthreshold) query must return a 'Value' column - "
                f"got {columns}"
            )

        breached = 0
        for row in rows:
            value = to_number(row[value_index])
            if value is None or value <= threshold:
                continue

            breached += 1
            name = str(row[name_index]) if name_index is not None else self.key
            details = str(row[details_index]) if details_index is not None else None

            self.add_alert(
                result,
                Alert(
                    name=name,
                    message=f"{name} value {value} exceeds threshold {threshold}",
                    value=value,
                    threshold=threshold,
                    details=details,
                ),
            )

        result.value = breached
        if result.status == ResultStatus.OK:
            result.message = (
                f"{len(rows)} row(s) checked, none exceeded threshold {threshold}"
            )


class DbNoRowsTest(DbTestBase):
    """Alerts when a query returns any rows, listing them in the alert details."""

    test_type = "dbnorows"
    default_max_rows = 50

    def execute(self, result: TestResult):
        columns, rows = self.run_query()
        result.value = len(rows)

        if not rows:
            result.message = "Query returned no rows"
            return

        summary = f"Query returned {len(rows)} row(s)"
        result.message = summary
        self.add_alert(
            result,
            Alert(
                name=self.key,
                message=summary,
                value=len(rows),
                details=self.format_rows(columns, rows, self.max_rows()),
            ),
        )

    def max_rows(self) -> int:
        value = to_number(self.option("max_rows", self.default_max_rows))
        if value is None:
            raise ValueError(f"Test '{self.key}' has a non-numeric max_rows")

        return int(value)

    @classmethod
    def format_rows(
        cls, columns: list[str], rows: list[list], max_rows: int | None = None
    ) -> str:
        shown = rows if max_rows is None or max_rows < 0 else rows[:max_rows]

        lines = [" | ".join(cls.cell(c) for c in columns)] if columns else []
        lines.extend(" | ".join(cls.cell(v) for v in row) for row in shown)
        if len(shown) < len(rows):
            lines.append(f"... and {len(rows) - len(shown)} more row(s)")

        return "\n".join(lines)

    @staticmethod
    def cell(value) -> str:
        """One row per line, so newlines and the column separator are neutralised."""
        if value is None:
            return ""

        text_value = str(value).replace("|", "\\|")

        return " ".join(text_value.split())


class DbReportTest(DbTestBase):
    """Produces a table report of everything the query returns."""

    test_type = "dbreport"

    def execute(self, result: TestResult):
        columns, rows = self.run_query()

        notify_if_empty = self.option("notify_if_empty", False)
        if rows or notify_if_empty:
            self.add_report(
                result,
                Report(title=self.key, columns=[str(c) for c in columns], rows=rows),
            )
        result.value = len(rows)
