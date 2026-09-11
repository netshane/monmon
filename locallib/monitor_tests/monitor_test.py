"""Base class for all monitor tests."""

from __future__ import annotations

import time as time_module
from abc import ABC, abstractmethod

from loguru import logger

from ..monitor_models import TestConfig
from ..monitor_results import Alert, Report, ResultStatus, TestResult
from ..value_expander import ValueExpander


class MonitorTest(ABC):
    """A single runnable test.

    Subclasses implement `execute` and either mutate the supplied result or
    return a new one.  Timing and error handling live here so every test type
    reports failures the same way.
    """

    test_type: str = "unknown"

    def __init__(self, config: TestConfig, value_expander: ValueExpander):
        self.config = config
        self.value_expander = value_expander

    @property
    def key(self) -> str:
        return self.config.key

    def run(self) -> TestResult:
        result = TestResult(key=self.key, test_type=self.config.test_type)
        started = time_module.monotonic()

        try:
            self.execute(result)
        except Exception as e:
            logger.exception(f"Test '{self.key}' ({self.config.test_type}) failed: {e}")
            result.status = ResultStatus.ERROR
            result.error = f"{type(e).__name__}: {e}"
            result.message = f"Test raised {type(e).__name__}: {e}"
        finally:
            result.duration_seconds = round(time_module.monotonic() - started, 4)

        return result

    @abstractmethod
    def execute(self, result: TestResult):
        """Perform the test, recording alerts / reports on `result`."""

    # -- helpers shared by subclasses ------------------------------------

    def option(self, name: str, default=None, expand: bool = True):
        value = self.config.get(name, default)
        if expand:
            return self.value_expander.expand(value)

        return value

    def required_option(self, name: str, expand: bool = True):
        value = self.option(name, expand=expand)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(
                f"Test '{self.key}' ({self.config.test_type}) requires a '{name}' setting"
            )

        return value

    @staticmethod
    def add_alert(result: TestResult, alert: Alert):
        result.alerts.append(alert)
        result.status = ResultStatus.ALERT
        if result.message is None:
            result.message = alert.message

    @staticmethod
    def add_report(result: TestResult, report: Report):
        result.reports.append(report)
        if result.message is None:
            result.message = f"{report.row_count} row(s) reported"
