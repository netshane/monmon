"""Loads and calls custom test commands from the custom_tests folder."""

from __future__ import annotations

import importlib.util
import os
from typing import Callable


class CustomTestLoader:
    """Imports a module from the custom_tests folder and returns a command.

    A custom test section is written as `[test.custom.<module>]` with a
    `command` naming the callable to invoke.  The callable receives the
    expanded `args` and returns either a dict (treated as an alert) or a list
    of dicts / rows (treated as a report).
    """

    def __init__(self, custom_tests_path: str):
        self.custom_tests_path = os.path.abspath(custom_tests_path)
        self._modules: dict[str, object] = {}

    def load_module(self, module_name: str):
        if module_name in self._modules:
            return self._modules[module_name]

        filename = module_name if module_name.endswith(".py") else f"{module_name}.py"
        path = os.path.join(self.custom_tests_path, filename)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Custom test module not found: {path}")

        spec = importlib.util.spec_from_file_location(
            f"custom_tests.{os.path.splitext(filename)[0]}", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load custom test module: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._modules[module_name] = module

        return module

    def get_command(self, module_name: str, command: str) -> Callable:
        module = self.load_module(module_name)
        func = getattr(module, command, None)
        if not callable(func):
            raise AttributeError(
                f"Custom test module '{module_name}' has no callable '{command}'"
            )

        return func
