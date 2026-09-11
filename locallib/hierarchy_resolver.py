"""Resolves monitor parent/child hierarchies and parent failure state."""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from .monitor_models import MonitorDefinition
from .monitor_results import ResultStatus


@dataclass
class ParentFailure:
    """A parent node that is currently in a failed state."""

    hierarchy: str
    parent_node: str
    monitor_names: list[str] = field(default_factory=list)
    skip: bool = False
    alert: bool = False


class HierarchyResolver:
    """Indexes monitors by hierarchy node and reports on parent failures.

    Each hierarchy (`[hierarchy]`, `[hierarchy.A]`, `[hierarchy.B]`, ...) is
    handled independently.  A node may be served by more than one monitor; the
    node counts as failed if any monitor providing it last failed.
    """

    def __init__(self, monitors: dict[str, MonitorDefinition]):
        self.monitors = monitors
        self.nodes: dict[str, dict[str, list[str]]] = {}
        self._build_index()

    def _build_index(self):
        for name, definition in self.monitors.items():
            for hierarchy_name, hierarchy in definition.hierarchies.items():
                if not hierarchy.node_name:
                    continue
                nodes = self.nodes.setdefault(hierarchy_name, {})
                nodes.setdefault(hierarchy.node_name, []).append(name)

    def validate(self) -> list[str]:
        """Return a warning for every parent reference that has no node.

        Cycles are reported too - a broken hierarchy should be visible rather
        than silently skipping monitors.
        """
        warnings: list[str] = []

        for name, definition in self.monitors.items():
            for hierarchy_name, hierarchy in definition.hierarchies.items():
                nodes = self.nodes.get(hierarchy_name, {})

                if hierarchy.parent_node and hierarchy.parent_node not in nodes:
                    warnings.append(
                        f"Monitor '{name}' hierarchy '{hierarchy_name}' references "
                        f"unknown parent node '{hierarchy.parent_node}'"
                    )

                if hierarchy.node_name and hierarchy.node_name == hierarchy.parent_node:
                    warnings.append(
                        f"Monitor '{name}' hierarchy '{hierarchy_name}' is its own parent"
                    )

        warnings.extend(self._find_cycles())

        for warning in warnings:
            logger.warning(warning)

        return warnings

    def _find_cycles(self) -> list[str]:
        warnings: list[str] = []

        for hierarchy_name, nodes in self.nodes.items():
            parents = self._parent_map(hierarchy_name)

            for node in nodes:
                seen = [node]
                current = parents.get(node)
                while current:
                    if current in seen:
                        chain = " -> ".join(seen + [current])
                        warning = f"Hierarchy '{hierarchy_name}' has a cycle: {chain}"
                        if warning not in warnings:
                            warnings.append(warning)
                        break
                    seen.append(current)
                    current = parents.get(current)

        return warnings

    def _parent_map(self, hierarchy_name: str) -> dict[str, str]:
        parents: dict[str, str] = {}

        for definition in self.monitors.values():
            hierarchy = definition.hierarchies.get(hierarchy_name)
            if hierarchy and hierarchy.node_name and hierarchy.parent_node:
                parents[hierarchy.node_name] = hierarchy.parent_node

        return parents

    def monitors_for_node(self, hierarchy_name: str, node_name: str) -> list[str]:
        return list(self.nodes.get(hierarchy_name, {}).get(node_name, []))

    def parent_failures(
        self,
        monitor_name: str,
        statuses: dict[str, ResultStatus | None],
    ) -> list[ParentFailure]:
        """Return failed parents of `monitor_name` across all its hierarchies.

        `statuses` maps monitor name to that monitor's last known status.
        """
        definition = self.monitors.get(monitor_name)
        if definition is None:
            return []

        failures: list[ParentFailure] = []

        for hierarchy_name, hierarchy in definition.hierarchies.items():
            if not hierarchy.parent_node:
                continue

            parent_monitors = self.monitors_for_node(
                hierarchy_name, hierarchy.parent_node
            )
            failed = [
                name
                for name in parent_monitors
                if statuses.get(name) is not None and statuses[name].is_failure
            ]

            if failed:
                failures.append(
                    ParentFailure(
                        hierarchy=hierarchy_name,
                        parent_node=hierarchy.parent_node,
                        monitor_names=failed,
                        skip=hierarchy.skip_on_parent_fail,
                        alert=hierarchy.alert_on_parent_fail,
                    )
                )

        return failures
