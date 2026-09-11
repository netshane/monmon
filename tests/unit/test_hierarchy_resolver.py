import pytest

from locallib.hierarchy_resolver import HierarchyResolver
from locallib.monitor_models import HierarchyConfig, MonitorDefinition
from locallib.monitor_results import ResultStatus

"""
Test: hierarchy resolution and parent failure handling
"""


def _monitor(name: str, hierarchies: dict[str, dict]) -> MonitorDefinition:
    return MonitorDefinition(
        name=name,
        source_file=f"{name}.toml",
        hierarchies={
            hierarchy_name: HierarchyConfig.from_dict(hierarchy_name, data)
            for hierarchy_name, data in hierarchies.items()
        },
    )


@pytest.fixture()
def monitors():
    return {
        "group": _monitor("group", {"primary": {"node_name": "server group 1"}}),
        "server": _monitor(
            "server",
            {
                "primary": {
                    "node_name": "server 123",
                    "parent_node": "server group 1",
                    "skip_on_parent_fail": True,
                },
                "A": {
                    "node_name": "app 123",
                    "parent_node": "app group 1",
                    "alert_on_parent_fail": True,
                },
            },
        ),
        "app_group": _monitor("app_group", {"A": {"node_name": "app group 1"}}),
    }


@pytest.mark.unit
def test_valid_hierarchies_produce_no_warnings(monitors):
    assert HierarchyResolver(monitors).validate() == []


@pytest.mark.unit
def test_missing_parent_node_is_warned_about(monitors):
    del monitors["group"]

    warnings = HierarchyResolver(monitors).validate()

    assert len(warnings) == 1
    assert "unknown parent node 'server group 1'" in warnings[0]


@pytest.mark.unit
def test_self_parenting_is_warned_about():
    monitors = {
        "loop": _monitor("loop", {"primary": {"node_name": "n", "parent_node": "n"}})
    }

    warnings = HierarchyResolver(monitors).validate()

    assert any("its own parent" in w for w in warnings)


@pytest.mark.unit
def test_cycles_are_warned_about():
    monitors = {
        "a": _monitor("a", {"primary": {"node_name": "a", "parent_node": "b"}}),
        "b": _monitor("b", {"primary": {"node_name": "b", "parent_node": "a"}}),
    }

    warnings = HierarchyResolver(monitors).validate()

    assert any("cycle" in w for w in warnings)


@pytest.mark.unit
def test_parent_failure_reports_skip_and_alert_flags(monitors):
    resolver = HierarchyResolver(monitors)
    statuses = {
        "group": ResultStatus.ERROR,
        "app_group": ResultStatus.ALERT,
        "server": ResultStatus.OK,
    }

    failures = {f.hierarchy: f for f in resolver.parent_failures("server", statuses)}

    assert sorted(failures) == ["A", "primary"]
    assert failures["primary"].skip is True
    assert failures["primary"].alert is False
    assert failures["primary"].monitor_names == ["group"]
    assert failures["A"].alert is True
    assert failures["A"].skip is False


@pytest.mark.unit
def test_healthy_parents_produce_no_failures(monitors):
    resolver = HierarchyResolver(monitors)
    statuses = {"group": ResultStatus.OK, "app_group": ResultStatus.OK}

    assert resolver.parent_failures("server", statuses) == []


@pytest.mark.unit
def test_parents_that_have_never_run_are_not_failures(monitors):
    resolver = HierarchyResolver(monitors)

    assert resolver.parent_failures("server", {"group": None}) == []


@pytest.mark.unit
def test_a_node_served_by_several_monitors_fails_if_any_fail():
    monitors = {
        "web1": _monitor("web1", {"primary": {"node_name": "web tier"}}),
        "web2": _monitor("web2", {"primary": {"node_name": "web tier"}}),
        "app": _monitor(
            "app",
            {
                "primary": {
                    "node_name": "app",
                    "parent_node": "web tier",
                    "skip_on_parent_fail": True,
                }
            },
        ),
    }
    resolver = HierarchyResolver(monitors)

    failures = resolver.parent_failures(
        "app", {"web1": ResultStatus.OK, "web2": ResultStatus.ALERT}
    )

    assert failures[0].monitor_names == ["web2"]


@pytest.mark.unit
def test_skipped_and_inactive_parents_are_not_failures():
    monitors = {
        "parent": _monitor("parent", {"primary": {"node_name": "p"}}),
        "child": _monitor("child", {"primary": {"node_name": "c", "parent_node": "p"}}),
    }
    resolver = HierarchyResolver(monitors)

    assert resolver.parent_failures("child", {"parent": ResultStatus.SKIPPED}) == []
    assert resolver.parent_failures("child", {"parent": ResultStatus.INACTIVE}) == []


@pytest.mark.unit
def test_monitors_for_node(monitors):
    resolver = HierarchyResolver(monitors)

    assert resolver.monitors_for_node("primary", "server group 1") == ["group"]
    assert resolver.monitors_for_node("A", "nope") == []
