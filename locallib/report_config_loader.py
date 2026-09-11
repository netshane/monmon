"""Finds and loads report definitions from the reports folder."""

from __future__ import annotations

import os

import yaml

from .report_models import ReportConfig, ReportConfigError

CONFIG_FILENAME = "report.yaml"
ALTERNATE_CONFIG_FILENAMES = ("report.yml",)


class ReportConfigLoader:
    """Loads `reports/<name>/report.yaml` definitions."""

    def __init__(self, reports_path: str):
        self.reports_path = os.path.abspath(reports_path)

    def report_names(self) -> list[str]:
        """Every report folder that holds a config file."""
        if not os.path.isdir(self.reports_path):
            return []

        names = []
        for entry in sorted(os.listdir(self.reports_path)):
            folder = os.path.join(self.reports_path, entry)
            if os.path.isdir(folder) and self._find_config(folder):
                names.append(entry)

        return names

    def load(self, name: str) -> ReportConfig:
        """Load one report definition by folder name."""
        folder = os.path.join(self.reports_path, name)
        config_path = self._find_config(folder)

        if not config_path:
            raise FileNotFoundError(
                f"No {CONFIG_FILENAME} found for report '{name}' (looked in {folder})"
            )

        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)

        config = ReportConfig.from_dict(name=name, path=folder, data=data or {})

        if not os.path.isfile(os.path.join(folder, config.template)):
            raise ReportConfigError(
                f"Report '{name}' template '{config.template}' was not found in {folder}"
            )

        return config

    def load_all(self) -> list[ReportConfig]:
        return [self.load(name) for name in self.report_names()]

    @staticmethod
    def _find_config(folder: str) -> str | None:
        for filename in (CONFIG_FILENAME, *ALTERNATE_CONFIG_FILENAMES):
            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                return path

        return None
