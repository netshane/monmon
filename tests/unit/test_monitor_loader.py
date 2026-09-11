import os

import pytest

from locallib.monitor_loader import MonitorLoader
from locallib.monitor_results import Alert

"""
Test: monitor definition loading and template resolution
"""

BASE = """
[settings]
name = "base"
description = "base description"
active = false
monitor_type_alert = true

[contact]
alert = ["email:base@example.com"]
error = ["email:errors@example.com"]

[schedule]
daily = true
"""

MIDDLE = """
[settings]
name = "middle"
template = "base.toml"
active = true

[schedule]
repeat = "5 min"
"""

CHILD = """
[settings]
name = "child"
description = "child description"
template = "sub/middle.toml"

[connections]
es_local = {host = "localhost", default_index = "logs*"}

[hierarchy]
node_name = "child node"
parent_node = "parent node"
skip_on_parent_fail = true

[hierarchy.A]
node_name = "child app"
parent_node = "app group"
alert_on_parent_fail = true

[test.ping]
key = "child.ping"
server = "example.com"

[test.custom.magic_py]
key = "child.custom"
command = "test_run"
args = ["123"]
"""


@pytest.fixture()
def monitors_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "base.toml").write_text(BASE)
    (tmp_path / "sub" / "middle.toml").write_text(MIDDLE)
    (tmp_path / "sub" / "child.toml").write_text(CHILD)
    (tmp_path / "notes.txt").write_text("not a monitor")

    return tmp_path


@pytest.mark.unit
def test_find_monitor_files_walks_subfolders(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    found = [os.path.basename(p) for p in loader.find_monitor_files()]

    assert sorted(found) == ["base.toml", "child.toml", "middle.toml"]
    assert "notes.txt" not in found


@pytest.mark.unit
def test_template_chain_is_merged_with_child_winning(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    child = loader.load_file(str(monitors_dir / "sub" / "child.toml"))

    # from the child itself
    assert child.name == "child"
    assert child.description == "child description"
    # from the middle template
    assert child.active is True
    assert child.schedule.repeat == "5 min"
    # from the base template, through the middle template
    assert child.schedule.daily is True
    assert child.contact.alert[0].target == "base@example.com"


@pytest.mark.unit
def test_template_names_are_excluded_from_monitors(monitors_dir):
    loader = MonitorLoader(str(monitors_dir), template_names=("base.toml",))

    monitors = loader.load_all()

    assert sorted(monitors) == ["child", "middle"]


@pytest.mark.unit
def test_hierarchies_are_kept_separate(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    child = loader.load_file(str(monitors_dir / "sub" / "child.toml"))

    assert sorted(child.hierarchies) == ["A", "primary"]
    assert child.hierarchies["primary"].parent_node == "parent node"
    assert child.hierarchies["primary"].skip_on_parent_fail is True
    assert child.hierarchies["A"].node_name == "child app"
    assert child.hierarchies["A"].alert_on_parent_fail is True
    assert child.hierarchies["A"].skip_on_parent_fail is False


HIERARCHY_DEFAULTS = """
[settings]
name = "hd-monitor"

[hierarchy]
parent_node = "root"

[hierarchy.A]
parent_node = "root_a"

[hierarchy.B]
node_name = "explicit-b"
parent_node = "root_b"
"""


def _write_monitor(monitors_dir, text):
    path = monitors_dir / "hd.toml"
    path.write_text(text)
    return path


@pytest.mark.unit
def test_primary_node_name_defaults_to_monitor_name(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    monitor = loader.load_file(_write_monitor(monitors_dir, HIERARCHY_DEFAULTS))

    assert monitor.hierarchies["primary"].node_name == "hd-monitor"
    assert monitor.hierarchies["primary"].parent_node == "root"


@pytest.mark.unit
def test_alternate_node_name_defaults_to_monitor_name_and_hierarchy(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    monitor = loader.load_file(_write_monitor(monitors_dir, HIERARCHY_DEFAULTS))

    assert monitor.hierarchies["A"].node_name == "hd-monitor-A"
    assert monitor.hierarchies["B"].node_name == "explicit-b"


@pytest.mark.unit
def test_bare_primary_hierarchy_section_fails_to_load(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))
    text = '[settings]\nname = "hd-monitor"\n\n[hierarchy]\n'

    with pytest.raises(ValueError, match="bare \\[hierarchy\\]"):
        loader.load_file(_write_monitor(monitors_dir, text))


@pytest.mark.unit
def test_bare_alternate_hierarchy_section_fails_to_load(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))
    text = (
        '[settings]\nname = "hd-monitor"\n\n[hierarchy.A]\nskip_on_parent_fail = true\n'
    )

    with pytest.raises(ValueError, match="bare \\[hierarchy.A\\]"):
        loader.load_file(_write_monitor(monitors_dir, text))


@pytest.mark.unit
def test_self_referencing_parent_node_becomes_root_with_warning(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))
    text = (
        '[settings]\nname = "hd-monitor"\n\n[hierarchy]\nparent_node = "hd-monitor"\n'
    )

    monitor = loader.load_file(_write_monitor(monitors_dir, text))

    assert monitor.hierarchies["primary"].node_name == "hd-monitor"
    assert monitor.hierarchies["primary"].parent_node is None
    assert any("its own parent_node" in e for e in loader.load_errors)


@pytest.mark.unit
def test_tests_are_flattened_with_keys(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    child = loader.load_file(str(monitors_dir / "sub" / "child.toml"))
    by_type = {t.test_type: t for t in child.tests}

    assert sorted(by_type) == ["custom.magic_py", "ping"]
    assert by_type["ping"].key == "child.ping"
    assert by_type["ping"].get("server") == "example.com"
    assert by_type["custom.magic_py"].get("command") == "test_run"
    assert "key" not in by_type["ping"].options


@pytest.mark.unit
def test_connections_are_loaded(monitors_dir):
    loader = MonitorLoader(str(monitors_dir))

    child = loader.load_file(str(monitors_dir / "sub" / "child.toml"))

    assert child.connections["es_local"]["host"] == "localhost"


@pytest.mark.unit
def test_absolute_template_paths_are_supported(tmp_path):
    template = tmp_path / "elsewhere" / "shared.toml"
    template.parent.mkdir()
    template.write_text('[settings]\nname = "shared"\nactive = false\n')

    monitors = tmp_path / "monitors"
    monitors.mkdir()
    (monitors / "uses_absolute.toml").write_text(
        f'[settings]\nname = "uses_absolute"\ntemplate = "{template}"\n'
    )

    definition = MonitorLoader(str(monitors)).load_file(
        str(monitors / "uses_absolute.toml")
    )

    assert definition.name == "uses_absolute"
    assert definition.active is False


@pytest.mark.unit
def test_template_cycles_are_reported(tmp_path):
    (tmp_path / "a.toml").write_text('[settings]\nname = "a"\ntemplate = "b.toml"\n')
    (tmp_path / "b.toml").write_text('[settings]\nname = "b"\ntemplate = "a.toml"\n')

    with pytest.raises(ValueError, match="cycle"):
        MonitorLoader(str(tmp_path)).load_file(str(tmp_path / "a.toml"))


@pytest.mark.unit
def test_missing_template_is_reported(tmp_path):
    (tmp_path / "a.toml").write_text('[settings]\nname = "a"\ntemplate = "gone.toml"\n')

    with pytest.raises(FileNotFoundError):
        MonitorLoader(str(tmp_path)).load_file(str(tmp_path / "a.toml"))


@pytest.mark.unit
def test_duplicate_monitor_names_keep_the_first(tmp_path):
    (tmp_path / "one.toml").write_text('[settings]\nname = "dup"\n')
    (tmp_path / "two.toml").write_text('[settings]\nname = "dup"\n')

    loader = MonitorLoader(str(tmp_path))
    monitors = loader.load_all()

    assert list(monitors) == ["dup"]
    assert monitors["dup"].source_file.endswith("one.toml")
    # the dropped file is a reported problem, not a silent skip
    assert len(loader.load_errors) == 1
    assert "Duplicate monitor name 'dup'" in loader.load_errors[0]


@pytest.mark.unit
def test_unloadable_file_does_not_stop_the_others(tmp_path):
    (tmp_path / "good.toml").write_text('[settings]\nname = "good"\n')
    (tmp_path / "bad.toml").write_text("this is not toml = = =")
    (tmp_path / "nameless.toml").write_text('[settings]\ndescription = "no name"\n')

    loader = MonitorLoader(str(tmp_path))
    monitors = loader.load_all()

    assert list(monitors) == ["good"]
    assert len(loader.load_errors) == 2


@pytest.mark.unit
def test_an_invalid_contact_fails_the_monitor_load(tmp_path):
    (tmp_path / "bad_contact.toml").write_text(
        '[settings]\nname = "bad"\n[contact]\nalert = ["slack:oops"]\n'
    )

    loader = MonitorLoader(str(tmp_path))

    assert loader.load_all() == {}
    assert "slack:oops" in loader.load_errors[0]


@pytest.mark.unit
def test_an_invalid_file_contact_fails_the_monitor_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("readonly")
    os.chmod("readonly", 0o500)
    (tmp_path / "bad_file.toml").write_text(
        '[settings]\nname = "bad"\n[contact]\nalert = ["file:readonly/report.txt"]\n'
    )

    loader = MonitorLoader(str(tmp_path))
    try:
        assert loader.load_all() == {}
        assert "readonly/report.txt" in loader.load_errors[0]
    finally:
        os.chmod("readonly", 0o700)


@pytest.mark.unit
def test_a_valid_file_contact_loads_the_monitor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "good_file.toml").write_text(
        '[settings]\nname = "good"\n[contact]\nalert = ["file:output/report.txt"]\n'
    )

    loader = MonitorLoader(str(tmp_path))
    monitors = loader.load_all()

    assert list(monitors) == ["good"]
    assert os.path.isfile("output/report.txt")


@pytest.mark.unit
def test_load_errors_are_reset_on_each_load(tmp_path):
    (tmp_path / "bad.toml").write_text("this is not toml = = =")
    loader = MonitorLoader(str(tmp_path))

    loader.load_all()
    loader.load_all()

    assert len(loader.load_errors) == 1


@pytest.mark.unit
def test_missing_monitors_folder_returns_nothing(tmp_path):
    loader = MonitorLoader(str(tmp_path / "does_not_exist"))

    assert loader.load_all() == {}


TEMPLATED_BASE = """
[settings]
name = "templated base"
active = true

[contact.message.alert.email]
subject = "base subject: {{ name }}"
body = "base body"
"""

TEMPLATED_CHILD = """
[settings]
name = "templated child"
template = "templated_base.toml"

[contact.message.alert.email]
body = "child body: {{ alert.message }}"
"""


@pytest.fixture()
def templated_dir(tmp_path):
    (tmp_path / "templated_base.toml").write_text(TEMPLATED_BASE)
    (tmp_path / "templated_child.toml").write_text(TEMPLATED_CHILD)

    return tmp_path


@pytest.mark.unit
def test_message_templates_are_compiled_at_load_time(templated_dir):
    loader = MonitorLoader(str(templated_dir))

    definition = loader.load_file(str(templated_dir / "templated_child.toml"))
    message = definition.message_templates.render(
        "alert", "email", {"name": "child", "alert": Alert(message="down")}
    )

    # the child overrode only `body`, so the template's subject survives
    assert message.subject == "base subject: child"
    assert message.body == "child body: down"


@pytest.mark.unit
def test_a_broken_message_template_fails_the_monitor_load(tmp_path):
    (tmp_path / "broken.toml").write_text(
        '[settings]\nname = "broken"\n\n'
        '[contact.message.alert.email]\nbody = "{% for x in %}"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    monitors = loader.load_all()

    assert monitors == {}
    assert len(loader.load_errors) == 1
    assert "Invalid 'body' template" in loader.load_errors[0]


@pytest.mark.unit
def test_a_monitor_may_not_set_a_batch_subject(tmp_path):
    (tmp_path / "batchy.toml").write_text(
        '[settings]\nname = "batchy"\n\n'
        '[contact.message.alert.email]\nbatch_subject = "nope"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    loader.load_all()

    assert "batch_subject" in loader.load_errors[0]


INSTANCES = """
[settings]
name = "instances"

[test.html_200.instance1]
key = "x.y.z"
url = "https://example.com"

[test.html_200.instance2]
key = "x.y.a"
url = "https://2.example.com"

[test.html_200.instance3]
url = "https://another.example.com"

[test.custom.magic_py.instance1]
key = "x.y.custom"
command = "test_run"

[test.ping]
key = "x.y.ping"
server = "example.com"
"""


@pytest.mark.unit
def test_instances_of_one_test_type_all_load(tmp_path):
    (tmp_path / "m.toml").write_text(INSTANCES)
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))
    html_tests = [t for t in definition.tests if t.test_type == "html_200"]

    # the instance suffix names the test, it is not part of the type
    assert [t.instance for t in html_tests] == ["instance1", "instance2", "instance3"]
    assert [t.key for t in html_tests][:2] == ["x.y.z", "x.y.a"]
    assert html_tests[0].get("url") == "https://example.com"
    assert html_tests[0].full_name == "html_200.instance1"


@pytest.mark.unit
def test_an_instance_without_a_key_is_named_after_its_instance(tmp_path):
    (tmp_path / "m.toml").write_text(INSTANCES)
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))
    by_instance = {t.instance: t for t in definition.tests if t.test_type == "html_200"}

    assert by_instance["instance3"].key == "instances.html_200.instance3"


@pytest.mark.unit
def test_a_custom_test_takes_its_instance_from_the_fourth_segment(tmp_path):
    (tmp_path / "m.toml").write_text(INSTANCES)
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))
    custom = [t for t in definition.tests if t.test_type == "custom.magic_py"]

    assert len(custom) == 1
    assert custom[0].instance == "instance1"
    assert custom[0].full_name == "custom.magic_py.instance1"


@pytest.mark.unit
def test_a_test_without_an_instance_is_the_default_instance(tmp_path):
    (tmp_path / "m.toml").write_text(INSTANCES)
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))
    ping = next(t for t in definition.tests if t.test_type == "ping")

    assert ping.instance == "default"
    # the instance is a placeholder here, so it stays out of messages
    assert ping.full_name == "ping"


@pytest.mark.unit
def test_a_test_without_an_instance_or_a_key_is_still_named(tmp_path):
    (tmp_path / "m.toml").write_text(
        '[settings]\nname = "m"\n\n[test.ping]\nserver = "a"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))

    assert definition.tests[0].key == "m.ping.default"


@pytest.mark.unit
def test_a_duplicate_test_key_keeps_the_last_test(tmp_path):
    (tmp_path / "m.toml").write_text(
        '[settings]\nname = "m"\n\n'
        '[test.html_200.first]\nkey = "shared"\nurl = "https://one.example.com"\n\n'
        '[test.html_200.middle]\nkey = "other"\nurl = "https://mid.example.com"\n\n'
        '[test.html_200.second]\nkey = "shared"\nurl = "https://two.example.com"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))

    # the survivor keeps its own position rather than the one it replaced
    assert [t.instance for t in definition.tests] == ["middle", "second"]
    assert definition.tests[1].get("url") == "https://two.example.com"
    assert len(loader.load_errors) == 1
    assert "Duplicate test key 'shared'" in loader.load_errors[0]
    assert "html_200.first" in loader.load_errors[0]


@pytest.mark.unit
def test_a_template_test_is_replaced_by_a_monitor_reusing_its_key(tmp_path):
    (tmp_path / "base.toml").write_text(
        '[settings]\nname = "base"\n\n'
        '[test.html_200.template]\nkey = "shared"\nurl = "https://template.example.com"\n'
    )
    (tmp_path / "child.toml").write_text(
        '[settings]\nname = "child"\ntemplate = "base.toml"\n\n'
        '[test.html_200.override]\nkey = "shared"\nurl = "https://child.example.com"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "child.toml"))

    assert [t.instance for t in definition.tests] == ["override"]
    assert definition.tests[0].get("url") == "https://child.example.com"


@pytest.mark.unit
def test_a_test_key_shared_between_monitors_is_reported_but_not_skipped(tmp_path):
    for name in ("one", "two"):
        (tmp_path / f"{name}.toml").write_text(
            f'[settings]\nname = "{name}"\n\n'
            '[test.ping.probe]\nkey = "shared"\nserver = "a"\n'
        )
    loader = MonitorLoader(str(tmp_path))

    monitors = loader.load_all()

    assert [len(m.tests) for m in monitors.values()] == [1, 1]
    assert len(loader.load_errors) == 1
    assert "Test key 'shared' is used by more than one monitor" in loader.load_errors[0]
    assert "one, two" in loader.load_errors[0]


@pytest.mark.unit
def test_an_array_of_tables_is_reported_rather_than_loaded(tmp_path):
    (tmp_path / "m.toml").write_text(
        '[settings]\nname = "m"\n\n'
        '[[test.html_200]]\nkey = "x"\nurl = "https://one.example.com"\n\n'
        '[[test.html_json_value.instance1]]\nkey = "y"\nurl = "https://two.example.com"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))

    assert definition.tests == []
    assert len(loader.load_errors) == 2
    assert all("is an array of tables" in e for e in loader.load_errors)


@pytest.mark.unit
def test_a_table_option_stays_an_option_rather_than_becoming_an_instance(tmp_path):
    (tmp_path / "m.toml").write_text(
        '[settings]\nname = "m"\n\n'
        "[test.html_200.web]\n"
        'key = "m.web"\n'
        'url = "https://example.com"\n'
        'headers = { Accept = "application/json" }\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))

    # the walk stops at the instance, so `headers` is this test's option and
    # not a phantom `html_200.headers` test
    assert len(definition.tests) == 1
    assert definition.tests[0].get("headers") == {"Accept": "application/json"}
    assert loader.load_errors == []


@pytest.mark.unit
def test_an_option_holding_a_list_of_tables_is_kept(tmp_path):
    (tmp_path / "m.toml").write_text(
        '[settings]\nname = "m"\n\n'
        "[test.custom.magic_py.orders]\n"
        'key = "m.orders"\n'
        'command = "run"\n'
        'args = [{ name = "one" }]\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))

    assert definition.tests[0].get("args") == [{"name": "one"}]
    assert loader.load_errors == []


@pytest.mark.unit
def test_an_array_of_tables_under_an_instance_is_reported(tmp_path):
    (tmp_path / "m.toml").write_text(
        '[settings]\nname = "m"\n\n'
        '[[test.html_200.web]]\nkey = "m.web"\nurl = "https://example.com"\n'
    )
    loader = MonitorLoader(str(tmp_path))

    definition = loader.load_file(str(tmp_path / "m.toml"))

    assert definition.tests == []
    assert "is an array of tables" in loader.load_errors[0]
