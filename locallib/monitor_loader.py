"""Loads monitor definitions from toml files, resolving template chains."""

from __future__ import annotations

import os

import toml
from loguru import logger

from .message_templates import MessageTemplateFactory
from .monitor_models import (
    DEFAULT_TEST_INSTANCE,
    ContactConfig,
    HierarchyConfig,
    MonitorDefinition,
    ScheduleConfig,
    TestConfig,
    parse_tags,
)


class MonitorLoader:
    """Loads every toml file under the monitors folder as a monitor definition.

    A file may name a `template` in its `[settings]` section.  Template files
    are merged underneath the monitor's own settings (the monitor wins), and
    templates may chain by naming a template of their own.  Template paths are
    resolved relative to the monitors folder, relative to the file that
    references them, or as absolute paths.
    """

    SECTION_MERGE_KEYS = ("settings", "connections", "schedule", "contact")

    def __init__(
        self,
        monitors_path: str,
        template_names: tuple[str, ...] = (),
        message_template_factory: MessageTemplateFactory | None = None,
    ):
        self.monitors_path = os.path.abspath(monitors_path)
        # message templates are compiled here so a broken template fails the
        # monitor load rather than the notification
        self.message_template_factory = (
            message_template_factory or MessageTemplateFactory()
        )
        # files whose basename is listed here are excluded from being loaded
        # as monitors in their own right - templates, and non-monitor files
        # such as the annotated sample.toml
        self.template_names = tuple(n.lower() for n in template_names)
        # problems from the last load_all - surfaced by `validate`
        self.load_errors: list[str] = []

    def load_all(self) -> dict[str, MonitorDefinition]:
        """Load all monitor definitions keyed by monitor name."""
        monitors: dict[str, MonitorDefinition] = {}
        self.load_errors = []

        for path in self.find_monitor_files():
            if os.path.basename(path).lower() in self.template_names:
                logger.debug(f"Skipping excluded file {path}")
                continue

            try:
                definition = self.load_file(path)
            except Exception as e:
                message = f"Failed to load monitor file '{path}': {e}"
                self.load_errors.append(message)
                logger.error(message)
                continue

            if definition.name in monitors:
                message = (
                    f"Duplicate monitor name '{definition.name}' in '{path}' - "
                    f"already defined in '{monitors[definition.name].source_file}'"
                )
                self.load_errors.append(message)
                logger.warning(message)
                continue

            monitors[definition.name] = definition

        self._check_global_test_keys(monitors)

        return monitors

    def find_monitor_files(self) -> list[str]:
        """Return every toml file under the monitors folder, recursively."""
        if not os.path.isdir(self.monitors_path):
            logger.warning(f"Monitors folder does not exist: {self.monitors_path}")
            return []

        found: list[str] = []
        for root, _dirs, files in os.walk(self.monitors_path):
            for filename in sorted(files):
                if filename.lower().endswith(".toml"):
                    found.append(os.path.join(root, filename))

        return sorted(found)

    def load_file(self, path: str) -> MonitorDefinition:
        """Load a single monitor file with its template chain resolved."""
        merged = self._load_with_templates(path, seen=[])
        return self._build_definition(path, merged)

    def _load_with_templates(self, path: str, seen: list[str]) -> dict:
        abspath = os.path.abspath(path)
        if abspath in seen:
            chain = " -> ".join(seen + [abspath])
            raise ValueError(f"Template cycle detected: {chain}")

        if not os.path.isfile(abspath):
            raise FileNotFoundError(f"Monitor file not found: {abspath}")

        data = toml.load(abspath)

        template = (data.get("settings") or {}).get("template")
        if not template:
            return data

        template_path = self._resolve_template_path(str(template), abspath)
        base = self._load_with_templates(template_path, seen + [abspath])

        return self._merge(base, data)

    def _resolve_template_path(self, template: str, referencing_file: str) -> str:
        candidates = []
        if os.path.isabs(template):
            candidates.append(template)
        else:
            candidates.append(os.path.join(self.monitors_path, template))
            candidates.append(os.path.join(os.path.dirname(referencing_file), template))

        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        raise FileNotFoundError(
            f"Template '{template}' referenced by '{referencing_file}' was not found "
            f"(looked in: {', '.join(candidates)})"
        )

    @classmethod
    def _merge(cls, base: dict, override: dict) -> dict:
        """Deep merge `override` on top of `base`."""
        result = dict(base)
        for key, value in override.items():
            existing = result.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                result[key] = cls._merge(existing, value)
            else:
                result[key] = value

        return result

    def _build_definition(self, path: str, data: dict) -> MonitorDefinition:
        settings = data.get("settings") or {}

        name = settings.get("name")
        if not name:
            raise ValueError(f"Monitor '{path}' has no [settings] name")

        hierarchies = self._build_hierarchies(str(name), data.get("hierarchy"))
        tests = self._build_tests(str(name), data.get("test") or {})
        contact_section = data.get("contact") or {}
        message_templates = self.message_template_factory.build(
            contact_section.get("message") or {}
        )

        # a monitor's own `template` key is informational once merged; drop the
        # merged-in template name from the child so it is not re-resolved
        return MonitorDefinition(
            name=str(name),
            source_file=os.path.abspath(path),
            description=settings.get("description"),
            link=settings.get("link"),
            template=settings.get("template"),
            monitor_type_alert=bool(settings.get("monitor_type_alert", True)),
            monitor_type_report=bool(settings.get("monitor_type_report", False)),
            active=bool(settings.get("active", True)),
            tags=parse_tags(settings.get("tags")),
            connections=dict(data.get("connections") or {}),
            schedule=ScheduleConfig.from_dict(data.get("schedule") or {}),
            hierarchies=hierarchies,
            contact=ContactConfig.from_dict(contact_section),
            tests=tests,
            message_templates=message_templates,
            raw=data,
        )

    def _build_hierarchies(
        self, monitor_name: str, section: dict | None
    ) -> dict[str, HierarchyConfig]:
        """Split `[hierarchy]` and `[hierarchy.X]` into separate hierarchies.

        A missing `node_name` defaults to the monitor name for the primary
        hierarchy, or `<monitor name>-<hierarchy name>` for an alternate.  A
        section that declares neither `node_name` nor `parent_node` is a bare
        section and is rejected - a root node must name itself.  A `parent_node`
        that resolves to the hierarchy's own node is treated as a root node
        (the parent reference is dropped) with a warning.
        """
        if section is None:
            return {}

        primary = {k: v for k, v in section.items() if not isinstance(v, dict)}
        alternates = {k: v for k, v in section.items() if isinstance(v, dict)}
        hierarchies: dict[str, HierarchyConfig] = {}

        # toml cannot tell `[hierarchy]` followed by `[hierarchy.A]` from
        # `[hierarchy.A]` alone, so the primary is "declared" only when it has
        # its own keys or there are no alternates to explain the section
        if primary or not alternates:
            hierarchies["primary"] = self._hierarchy_config(
                monitor_name, "primary", primary, default_node_name=monitor_name
            )

        for key, value in alternates.items():
            hierarchies[key] = self._hierarchy_config(
                monitor_name,
                key,
                value,
                default_node_name=f"{monitor_name}-{key}",
            )

        return hierarchies

    def _hierarchy_config(
        self,
        monitor_name: str,
        hierarchy_name: str,
        data: dict,
        default_node_name: str,
    ) -> HierarchyConfig:
        config = HierarchyConfig.from_dict(hierarchy_name, data)

        label = (
            "hierarchy"
            if hierarchy_name == "primary"
            else f"hierarchy.{hierarchy_name}"
        )
        if not config.node_name and not config.parent_node:
            raise ValueError(
                f"bare [{label}] section: set 'node_name' (root node) or 'parent_node'"
            )

        if not config.node_name:
            config.node_name = default_node_name

        if config.parent_node and config.parent_node == config.node_name:
            self._record_error(
                f"Monitor '{monitor_name}' hierarchy '{hierarchy_name}' lists itself "
                f"as its own parent_node - treating it as a root node"
            )
            config.parent_node = None

        return config

    def _record_error(self, message: str):
        """Record a problem for `validate` without failing the load."""
        self.load_errors.append(message)
        logger.warning(message)

    def _check_global_test_keys(self, monitors: dict[str, MonitorDefinition]):
        """Warn when one test key is used by more than one monitor.

        The key identifies a result, and report field selectors match on it, so
        sharing one across monitors makes a report ambiguous.  Unlike a
        duplicate inside a single monitor nothing is skipped - both tests still
        run.
        """
        owners: dict[str, list[str]] = {}
        for definition in monitors.values():
            for test in definition.tests:
                owners.setdefault(test.key, []).append(definition.name)

        for key, names in sorted(owners.items()):
            if len(names) > 1:
                self._record_error(
                    f"Test key '{key}' is used by more than one monitor: "
                    f"{', '.join(sorted(names))}"
                )

    def _build_tests(self, monitor_name: str, section: dict) -> list[TestConfig]:
        tests: list[TestConfig] = []

        for full_name, options in self._flatten_tests(monitor_name, section):
            test_type, instance = self._split_test_name(full_name)

            key = options.get("key")
            if not key:
                key = f"{monitor_name}.{test_type}.{instance}"
                logger.warning(
                    f"Monitor '{monitor_name}' test '{full_name}' has no key - "
                    f"using '{key}' as the result key"
                )

            tests.append(
                TestConfig(
                    test_type=test_type,
                    key=str(key),
                    options={k: v for k, v in options.items() if k != "key"},
                    instance=instance,
                )
            )

        return self._drop_duplicate_keys(monitor_name, tests)

    @staticmethod
    def _split_test_name(full_name: str) -> tuple[str, str]:
        """Split a `[test.*]` section path into its test type and instance.

        `[test.html_200]` is `('html_200', 'default')` and
        `[test.html_200.web]` is `('html_200', 'web')`.  A custom test carries
        its module name in the type, so its instance sits one segment further
        along: `[test.custom.magic_py.web]` is `('custom.magic_py', 'web')`.
        """
        parts = full_name.split(".")
        depth = 2 if parts[0].lower() == "custom" and len(parts) > 1 else 1

        return ".".join(parts[:depth]), ".".join(parts[depth:]) or DEFAULT_TEST_INSTANCE

    def _drop_duplicate_keys(
        self, monitor_name: str, tests: list[TestConfig]
    ) -> list[TestConfig]:
        """Keep only the last test defined for each key, in its own position.

        Template sections are merged underneath the monitor's own, so the last
        definition is the most specific one - a monitor replaces a template's
        test by reusing its key.
        """
        last_index = {test.key: index for index, test in enumerate(tests)}

        kept: list[TestConfig] = []
        for index, test in enumerate(tests):
            winner = tests[last_index[test.key]]
            if winner is not test:
                self._record_error(
                    f"Duplicate test key '{test.key}' in monitor '{monitor_name}' - "
                    f"test '{test.full_name}' was skipped in favour of "
                    f"'{winner.full_name}'"
                )
                continue

            kept.append(test)

        return kept

    def _flatten_tests(self, monitor_name: str, section: dict):
        """Yield (section path, options) pairs for every `[test.*]` section."""
        for name, value in section.items():
            # a custom test spends a section level on its module name, so its
            # instance sits one level deeper than every other test type
            levels = 2 if str(name).lower() == "custom" else 1
            yield from self._flatten_section(monitor_name, str(name), value, levels)

    def _flatten_section(self, monitor_name: str, full_name: str, value, levels: int):
        """Yield the tests declared by one `[test.*]` section.

        `levels` is how many nested sections may still follow: one for the
        instance name, two for a custom test's module and its instance.  A
        table nested deeper than that is an option (`headers = {...}`), not
        another test, so the walk stops there and hands the whole table over
        as the test's options.
        """
        if self._is_table_array(value):
            self._table_array_error(monitor_name, full_name)
            return

        if not isinstance(value, dict):
            return

        if levels <= 0:
            if value:
                yield full_name, value
            return

        nested: dict = {}
        options: dict = {}
        for option, option_value in value.items():
            if isinstance(option_value, dict) or self._is_table_array(option_value):
                nested[option] = option_value
            else:
                options[option] = option_value

        if options:
            yield full_name, options

        for child, child_value in nested.items():
            yield from self._flatten_section(
                monitor_name, f"{full_name}.{child}", child_value, levels - 1
            )

    @staticmethod
    def _is_table_array(value) -> bool:
        """True for a toml `[[test.x]]` array of tables."""
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, dict) for item in value)
        )

    def _table_array_error(self, monitor_name: str, full_name: str):
        self._record_error(
            f"Monitor '{monitor_name}' test '{full_name}' is an array of tables - "
            f"declare each test as its own '[test.<test_type>.<instance>]' section "
            f"rather than '[[test.{full_name}]]'"
        )
