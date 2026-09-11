import pytest

import locallib.dependencies as deps
from locallib.dependencies import (
    get_settings,
    set_settings_extenstion_file,
    set_settings_file,
)

"""
Test: `--extend` settings file path resolution

Path-like settings (monitors_path, custom_tests_path, reports_path,
reports_output_path, results_db) that are overridden in the `--extend`
settings file should resolve relative to that file's directory. Anything
left to the primary settings file should keep resolving relative to the
application working directory.
"""


@pytest.fixture(autouse=True)
def _reset_settings_state(monkeypatch):
    monkeypatch.setattr(deps, "__cached_settings", None)
    monkeypatch.setattr(
        deps,
        "settings_options",
        {
            "prd": False,
            "dev": False,
            "force_env": None,
            "settings_file": "./settings.toml",
            "settings_extension_file": None,
        },
    )
    monkeypatch.setenv("ASPNETCORE_ENVIRONMENT", "default")


def _write_settings(path, contents: str):
    path.write_text(contents)
    return str(path)


@pytest.mark.unit
def test_extend_file_paths_resolve_relative_to_extend_dir(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    ext_dir = tmp_path / "shared" / "config"
    ext_dir.mkdir(parents=True)

    settings_file = _write_settings(
        work_dir / "settings.toml",
        """
[default]
monitors_path = "./monitors_base"
custom_tests_path = "./custom_tests_base"
reports_path = "./reports_base"
reports_output_path = "./output_base"
results_db = "sqlite:///monitors_base.db"
""",
    )
    extend_file = _write_settings(
        ext_dir / "extend.toml",
        """
[default]
monitors_path = "./monitors_ext"
results_db = "sqlite:///monitors_ext.db"
""",
    )

    monkeypatch.chdir(work_dir)
    set_settings_file(settings_file)
    set_settings_extenstion_file(extend_file)

    settings = get_settings()

    # overridden in the extend file -> relative to the extend file's directory
    assert settings.monitors_path == str(ext_dir / "monitors_ext")
    assert settings.results_db == f"sqlite:///{ext_dir / 'monitors_ext.db'}"

    # not overridden in the extend file -> relative to the app working directory
    assert settings.custom_tests_path == "./custom_tests_base"
    assert settings.reports_path == "./reports_base"
    assert settings.reports_output_path == "./output_base"


@pytest.mark.unit
def test_absolute_paths_in_extend_file_are_left_unchanged(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    ext_dir = tmp_path / "shared"
    ext_dir.mkdir()
    absolute_monitors = tmp_path / "elsewhere" / "monitors"

    settings_file = _write_settings(
        work_dir / "settings.toml",
        """
[default]
monitors_path = "./monitors_base"
""",
    )
    extend_file = _write_settings(
        ext_dir / "extend.toml",
        f"""
[default]
monitors_path = "{absolute_monitors}"
""",
    )

    monkeypatch.chdir(work_dir)
    set_settings_file(settings_file)
    set_settings_extenstion_file(extend_file)

    settings = get_settings()

    assert settings.monitors_path == str(absolute_monitors)


@pytest.mark.unit
def test_local_file_override_wins_over_extend_and_stays_relative_to_cwd(
    tmp_path, monkeypatch
):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    ext_dir = tmp_path / "shared"
    ext_dir.mkdir()

    settings_file = _write_settings(
        work_dir / "settings.toml",
        """
[default]
monitors_path = "./monitors_base"
""",
    )
    extend_file = _write_settings(
        ext_dir / "extend.toml",
        """
[default]
monitors_path = "./monitors_ext"
""",
    )
    # settings.local.toml is merged after the extend file and overrides it
    _write_settings(
        work_dir / "settings.local.toml",
        """
[default]
monitors_path = "./monitors_local"
""",
    )

    monkeypatch.chdir(work_dir)
    set_settings_file(settings_file)
    set_settings_extenstion_file(extend_file)

    settings = get_settings()

    assert settings.monitors_path == "./monitors_local"


@pytest.mark.unit
def test_sqlite_in_memory_url_is_not_rebased(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    ext_dir = tmp_path / "shared"
    ext_dir.mkdir()

    settings_file = _write_settings(
        work_dir / "settings.toml",
        """
[default]
results_db = "sqlite:///monitors_base.db"
""",
    )
    extend_file = _write_settings(
        ext_dir / "extend.toml",
        """
[default]
results_db = "sqlite:///:memory:"
""",
    )

    monkeypatch.chdir(work_dir)
    set_settings_file(settings_file)
    set_settings_extenstion_file(extend_file)

    settings = get_settings()

    assert settings.results_db == "sqlite:///:memory:"


@pytest.mark.unit
def test_without_extend_file_paths_stay_relative_to_working_directory(
    tmp_path, monkeypatch
):
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    settings_file = _write_settings(
        work_dir / "settings.toml",
        """
[default]
monitors_path = "./monitors_base"
results_db = "sqlite:///monitors_base.db"
""",
    )

    monkeypatch.chdir(work_dir)
    set_settings_file(settings_file)

    settings = get_settings()

    assert settings.monitors_path == "./monitors_base"
    assert settings.results_db == "sqlite:///monitors_base.db"
