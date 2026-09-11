import pytest

from locallib.report_config_loader import ReportConfigLoader
from locallib.report_models import ReportConfigError

"""
Test: report definition discovery and loading
"""


def _write(folder, name: str, config: str, template: str | None = "<html></html>"):
    report = folder / name
    report.mkdir(parents=True)
    (report / "report.yaml").write_text(config)
    if template is not None:
        (report / "template.html.j2").write_text(template)

    return report


@pytest.mark.unit
def test_report_names_lists_folders_holding_a_config(tmp_path):
    _write(tmp_path, "default", 'title: "Default"\n')
    _write(tmp_path, "nightly", 'title: "Nightly"\n')
    (tmp_path / "not-a-report").mkdir()

    assert ReportConfigLoader(str(tmp_path)).report_names() == ["default", "nightly"]


@pytest.mark.unit
def test_missing_reports_folder_is_not_an_error(tmp_path):
    assert ReportConfigLoader(str(tmp_path / "nope")).report_names() == []


@pytest.mark.unit
def test_load_parses_the_definition(tmp_path):
    _write(
        tmp_path,
        "default",
        'title: "Nightly Jobs Overview"\n'
        "monitors:\n"
        '  - match: "backup-*"\n'
        "    fields:\n"
        "      status:\n"
        "        mode: latest\n"
        "      run_time:\n"
        "        mode: range\n"
        "        last: 30d\n",
    )

    config = ReportConfigLoader(str(tmp_path)).load("default")

    assert config.title == "Nightly Jobs Overview"
    assert config.monitors[0].match == ["backup-*"]
    assert config.monitors[0].fields["run_time"].is_range is True


@pytest.mark.unit
def test_load_all_returns_every_report(tmp_path):
    _write(tmp_path, "default", 'title: "Default"\n')
    _write(tmp_path, "nightly", 'title: "Nightly"\n')

    assert [c.name for c in ReportConfigLoader(str(tmp_path)).load_all()] == [
        "default",
        "nightly",
    ]


@pytest.mark.unit
def test_missing_report_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ReportConfigLoader(str(tmp_path)).load("nope")


@pytest.mark.unit
def test_missing_template_raises(tmp_path):
    _write(tmp_path, "default", 'title: "Default"\n', template=None)

    with pytest.raises(ReportConfigError):
        ReportConfigLoader(str(tmp_path)).load("default")


@pytest.mark.unit
def test_an_empty_config_falls_back_to_defaults(tmp_path):
    _write(tmp_path, "default", "")

    config = ReportConfigLoader(str(tmp_path)).load("default")

    assert config.title == "default"
    assert config.monitors == []
    assert config.section_enabled("summary") is True
