"""SQL Server Agent (SSA) job status monitor tests."""

from __future__ import annotations

from ..helpers import parse_interval
from ..monitor_results import Alert, Report, TestResult
from .db_tests import DbTestBase

_DEFAULT_TIMEFRAME = "1 day"

# `run_status = 0` is Failed; `step_id = 0` is the job level outcome row,
# not an individual step. Comparing `run_date`/`run_time` as one combined
# int avoids the case where a later date has a numerically smaller time.
_LATEST_RUN_QUERY = """
    SELECT TOP 1
        j.name AS JobName,
        h.run_status AS RunStatus,
        h.run_date AS RunDate,
        h.run_time AS RunTime,
        CASE h.run_status
            WHEN 0 THEN 'Failed'
            WHEN 1 THEN 'Succeeded'
            WHEN 2 THEN 'Retry'
            WHEN 3 THEN 'Canceled'
            WHEN 4 THEN 'In Progress'
        END AS RunStatusDescription
    FROM msdb.dbo.sysjobs j
    JOIN msdb.dbo.sysjobhistory h ON j.job_id = h.job_id
    WHERE j.name = :job_name
      AND h.step_id = 0
      AND (CAST(h.run_date AS BIGINT) * 1000000 + h.run_time) >= (CAST(:cutoff_date AS BIGINT) * 1000000 + :cutoff_time)
    ORDER BY h.run_date DESC, h.run_time DESC
"""

_ERROR_RUNS_QUERY = """
    SELECT
        j.name AS JobName,
        h.run_date AS RunDate,
        h.run_time AS RunTime,
        CASE h.run_status
            WHEN 0 THEN 'Failed'
            WHEN 1 THEN 'Succeeded'
            WHEN 2 THEN 'Retry'
            WHEN 3 THEN 'Canceled'
            WHEN 4 THEN 'In Progress'
        END AS RunStatus,
        h.message AS RunMessage
    FROM msdb.dbo.sysjobs j
    JOIN msdb.dbo.sysjobhistory h ON j.job_id = h.job_id
    WHERE j.name = :job_name
      AND h.run_status = 0
      AND h.step_id = 0
      AND (CAST(h.run_date AS BIGINT) * 1000000 + h.run_time) >= (CAST(:cutoff_date AS BIGINT) * 1000000 + :cutoff_time)
    ORDER BY h.run_date DESC, h.run_time DESC
"""


class SsaJobTestBase(DbTestBase):
    """Shared query execution for the SQL Server Agent job tests."""

    def run_ssa_query(self, query: str) -> tuple[list[str], list[list]]:
        job_name = self.required_option("job_name")

        timeframe = parse_interval(str(self.option("timeframe", _DEFAULT_TIMEFRAME)))
        if timeframe is None:
            raise ValueError(f"Test '{self.key}' has an invalid 'timeframe' setting")

        cutoff = self.value_expander.now_provider() - timeframe
        cutoff_date = int(cutoff.strftime("%Y%m%d"))
        cutoff_time = int(cutoff.strftime("%H%M%S"))

        return self.run_query(
            query,
            {
                "job_name": job_name,
                "cutoff_date": cutoff_date,
                "cutoff_time": cutoff_time,
            },
        )


class SsaJobSucceededTest(SsaJobTestBase):
    """Alerts unless the job's most recent run in the timeframe succeeded or is running."""

    test_type = "ssa_job_succeeded"
    ok_statuses = ("Succeeded", "In Progress")

    def execute(self, result: TestResult):
        job_name = self.required_option("job_name")
        columns, rows = self.run_ssa_query(_LATEST_RUN_QUERY)

        if not rows:
            self.add_alert(
                result,
                Alert(
                    name=self.key,
                    message=f"No run found for job '{job_name}' in the given timeframe",
                ),
            )
            return

        status_index = self.column_index(columns, "RunStatusDescription")
        if status_index is None:
            raise ValueError(
                f"Test '{self.key}' ({self.test_type}) query must return a "
                f"'RunStatusDescription' column - got {columns}"
            )
        status = str(rows[0][status_index])
        result.value = status
        message = f"Job '{job_name}' last run status is '{status}'"

        if status not in self.ok_statuses:
            self.add_alert(result, Alert(name=self.key, message=message, value=status))
            return

        result.message = message


class SsaJobErrorsTest(SsaJobTestBase):
    """Reports any failed runs of the job in the given timeframe."""

    test_type = "ssa_job_errors"

    def execute(self, result: TestResult):
        columns, rows = self.run_ssa_query(_ERROR_RUNS_QUERY)

        notify_if_empty = self.option("notify_if_empty", False)
        if rows or notify_if_empty:
            self.add_report(
                result,
                Report(title=self.key, columns=[str(c) for c in columns], rows=rows),
            )
        result.value = len(rows)
