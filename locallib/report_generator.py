"""Generates static html reports from stored monitor history."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from .report_config_loader import ReportConfigLoader
from .report_data_builder import ReportDataBuilder
from .report_models import ReportConfig
from .report_renderer import ReportRenderer

# copied verbatim into the output folder so a report can ship its own css,
# js, images or fonts and still work offline over file://
STATIC_FOLDER = "static"


@dataclass
class GeneratedReport:
    """Where a rendered report landed, and what went into it."""

    name: str
    title: str
    output_path: str
    monitor_count: int
    generated_at: datetime


class ReportGenerator:
    """Loads report definitions, resolves their data, and writes html."""

    def __init__(
        self,
        config_loader: ReportConfigLoader,
        data_builder: ReportDataBuilder,
        renderer: ReportRenderer,
        output_path: str = "./output",
    ):
        self.config_loader = config_loader
        self.data_builder = data_builder
        self.renderer = renderer
        self.output_path = os.path.abspath(output_path)

    def report_names(self) -> list[str]:
        return self.config_loader.report_names()

    def generate(
        self,
        name: str = "default",
        output_path: str | None = None,
        now: datetime | None = None,
    ) -> GeneratedReport:
        """Render one report to `<output>/<name>/index.html`."""
        config = self.config_loader.load(name)

        return self.generate_config(config, output_path=output_path, now=now)

    def generate_all(
        self, output_path: str | None = None, now: datetime | None = None
    ) -> list[GeneratedReport]:
        """Render every report found under the reports folder.

        One broken definition does not stop the others - the failure is logged
        and the remaining reports are still written.
        """
        names = self.report_names()
        if not names:
            logger.warning(
                f"No report definitions found in {self.config_loader.reports_path}"
            )

        # one timestamp for the whole run so all pages agree
        now = now or datetime.now()
        generated = []

        for name in names:
            try:
                generated.append(self.generate(name, output_path=output_path, now=now))
            except Exception as e:
                logger.exception(f"Failed to generate report '{name}': {e}")

        return generated

    def generate_config(
        self,
        config: ReportConfig,
        output_path: str | None = None,
        now: datetime | None = None,
    ) -> GeneratedReport:
        now = now or datetime.now()
        context = self.data_builder.build(config, now=now)
        html = self.renderer.render(config, context)

        folder = os.path.join(
            os.path.abspath(output_path or self.output_path), config.name
        )
        os.makedirs(folder, exist_ok=True)
        self._copy_static(config, folder)

        target = os.path.join(folder, "index.html")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(html)

        logger.info(
            f"Wrote report '{config.name}' covering "
            f"{len(context['monitors'])} monitor(s) to {target}"
        )

        return GeneratedReport(
            name=config.name,
            title=config.title,
            output_path=target,
            monitor_count=len(context["monitors"]),
            generated_at=now,
        )

    @staticmethod
    def _copy_static(config: ReportConfig, folder: str):
        source = os.path.join(config.path, STATIC_FOLDER)
        if not os.path.isdir(source):
            return

        shutil.copytree(source, os.path.join(folder, STATIC_FOLDER), dirs_exist_ok=True)
