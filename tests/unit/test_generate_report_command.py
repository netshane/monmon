from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import monmon
from locallib.report_generator import GeneratedReport

"""
Test: the generate-report cli command
"""


def _generated(name: str) -> GeneratedReport:
    return GeneratedReport(
        name=name,
        title=name.title(),
        output_path=f"/tmp/output/{name}/index.html",
        monitor_count=2,
        generated_at=datetime(2026, 8, 12, 12, 0, 0),
    )


@pytest.fixture()
def generator():
    generator = MagicMock()
    generator.generate.side_effect = lambda name, **kwargs: _generated(name)
    generator.report_names.return_value = ["default", "nightly"]
    generator.generate_all.return_value = [_generated("default"), _generated("nightly")]

    with patch("monmon.get_report_generator", return_value=generator) as factory:
        yield generator, factory


@pytest.mark.unit
def test_no_arguments_renders_the_default_report(generator):
    generator, factory = generator

    result = CliRunner().invoke(monmon.cli, ["--dev", "generate-report"], obj={})

    assert result.exit_code == 0
    generator.generate.assert_called_once_with("default")
    factory.assert_called_once_with(output_path=None, strict=False)
    assert "1 report(s) generated" in result.output


@pytest.mark.unit
def test_template_option_selects_the_report(generator):
    generator, _ = generator

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "generate-report", "--template", "nightly"], obj={}
    )

    assert result.exit_code == 0
    generator.generate.assert_called_once_with("nightly")


@pytest.mark.unit
def test_all_option_renders_every_report(generator):
    generator, _ = generator

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "generate-report", "--all"], obj={}
    )

    assert result.exit_code == 0
    generator.generate_all.assert_called_once_with()
    assert "2 report(s) generated" in result.output


@pytest.mark.unit
def test_output_option_is_passed_to_the_generator(generator):
    _, factory = generator

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "generate-report", "--output", "/tmp/elsewhere"], obj={}
    )

    assert result.exit_code == 0
    factory.assert_called_once_with(output_path="/tmp/elsewhere", strict=False)


@pytest.mark.unit
def test_all_reports_a_partial_failure(generator):
    generator, _ = generator
    # two definitions found, only one of them rendered
    generator.generate_all.return_value = [_generated("default")]

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "generate-report", "--all"], obj={}
    )

    assert result.exit_code == 1
    assert "1 report(s) generated" in result.output
    assert "1 report(s) failed" in result.output


@pytest.mark.unit
def test_all_with_no_reports_exits_nonzero(generator):
    generator, _ = generator
    generator.generate_all.return_value = []

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "generate-report", "--all"], obj={}
    )

    assert result.exit_code == 1
    assert "No reports were generated" in result.output
