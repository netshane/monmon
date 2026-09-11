import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from locallib.monitor_repository import MonitorRepository
from locallib.monitor_results import Alert, MonitorResult, ResultStatus, TestResult
from locallib.report_config_loader import ReportConfigLoader
from locallib.report_data_builder import ReportDataBuilder
from locallib.report_generator import ReportGenerator
from locallib.report_renderer import ReportRenderer, format_duration, sparkline

"""
Test: static html report generation

Renders the real reports/default definition against a temporary database.
"""

NOW = datetime(2026, 8, 12, 12, 0, 0)
PROJECT_REPORTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "reports",
)


@pytest.fixture()
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'monitors.db'}", future=True)
    repository = MonitorRepository(engine=engine)
    repository.create_schema()

    return repository


def _generator(repository, reports_path: str, output_path: str) -> ReportGenerator:
    return ReportGenerator(
        config_loader=ReportConfigLoader(reports_path=reports_path),
        data_builder=ReportDataBuilder(repository=repository),
        renderer=ReportRenderer(strict=True),
        output_path=output_path,
    )


def _write_report(folder, name: str, title: str, template: str | None = None):
    report = folder / name
    report.mkdir(parents=True)
    (report / "report.yaml").write_text(
        f'title: "{title}"\n'
        "monitors:\n"
        '  - match: "*"\n'
        "    fields:\n"
        "      status:\n"
        "        mode: latest\n"
        "      run_time:\n"
        "        mode: range\n"
        "        last: 30d\n"
    )
    (report / "template.html.j2").write_text(
        template
        or "<html><body><h1>{{ title }}</h1>"
        "{% for m in monitors %}<p>{{ m.name }}={{ m.fields.status.value }}</p>"
        "{% endfor %}</body></html>"
    )

    return report


def _save_run(repository, name: str, days_ago: float, status=ResultStatus.OK, **kwargs):
    started_at = NOW - timedelta(days=days_ago)

    repository.save_result(
        MonitorResult(
            monitor_name=name,
            status=status,
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=1.5),
            test_results=[
                TestResult(
                    key=f"{name}.ping", test_type="ping", status=status, **kwargs
                )
            ],
        )
    )


@pytest.mark.unit
def test_generate_writes_index_html(repository, tmp_path):
    _save_run(repository, "backup-db", days_ago=1)
    _write_report(tmp_path / "reports", "default", "Nightly Jobs Overview")

    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )
    generated = generator.generate()

    assert generated.output_path == str(tmp_path / "output" / "default" / "index.html")
    assert os.path.isfile(generated.output_path)

    html = open(generated.output_path, encoding="utf-8").read()
    assert "Nightly Jobs Overview" in html
    assert "backup-db=ok" in html


@pytest.mark.unit
def test_generate_all_renders_every_report(repository, tmp_path):
    _save_run(repository, "backup-db", days_ago=1)
    _write_report(tmp_path / "reports", "default", "Default")
    _write_report(tmp_path / "reports", "nightly", "Nightly")

    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )
    generated = generator.generate_all()

    assert sorted(g.name for g in generated) == ["default", "nightly"]
    for report in generated:
        assert os.path.isfile(report.output_path)


@pytest.mark.unit
def test_generate_can_render_one_report_by_name(repository, tmp_path):
    _write_report(tmp_path / "reports", "default", "Default")
    _write_report(tmp_path / "reports", "nightly", "Nightly")

    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )
    generator.generate("nightly")

    assert os.path.isfile(tmp_path / "output" / "nightly" / "index.html")
    assert not os.path.exists(tmp_path / "output" / "default")


@pytest.mark.unit
def test_output_directory_can_be_overridden(repository, tmp_path):
    _write_report(tmp_path / "reports", "default", "Default")

    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )
    generated = generator.generate("default", output_path=str(tmp_path / "elsewhere"))

    assert generated.output_path.startswith(str(tmp_path / "elsewhere"))


@pytest.mark.unit
def test_static_assets_are_copied_next_to_the_page(repository, tmp_path):
    report = _write_report(tmp_path / "reports", "default", "Default")
    (report / "static").mkdir()
    (report / "static" / "site.css").write_text("body { color: red; }")

    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )
    generator.generate()

    assert os.path.isfile(tmp_path / "output" / "default" / "static" / "site.css")


@pytest.mark.unit
def test_missing_report_raises(repository, tmp_path):
    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )

    with pytest.raises(FileNotFoundError):
        generator.generate("nope")


@pytest.mark.unit
def test_project_default_report_renders_with_data(repository, tmp_path):
    _save_run(repository, "backup-db", days_ago=2)
    _save_run(
        repository,
        "backup-db",
        days_ago=1,
        status=ResultStatus.ALERT,
        alerts=[Alert(name="disk", message="disk is full")],
    )
    _save_run(
        repository,
        "web-front",
        days_ago=1,
        status=ResultStatus.ERROR,
        error="connection refused",
    )

    generator = _generator(repository, PROJECT_REPORTS, str(tmp_path / "output"))
    generated = generator.generate("default", now=NOW)
    html = open(generated.output_path, encoding="utf-8").read()

    assert generated.monitor_count == 2
    assert "disk is full" in html
    assert "connection refused" in html
    # self contained - nothing is fetched over the network
    assert "http://" not in html and "https://" not in html
    assert "<svg" in html


@pytest.mark.unit
def test_project_default_report_renders_empty_database(repository, tmp_path):
    generator = _generator(repository, PROJECT_REPORTS, str(tmp_path / "output"))
    generated = generator.generate("default", now=NOW)
    html = open(generated.output_path, encoding="utf-8").read()

    assert generated.monitor_count == 0
    assert "No errors recorded" in html
    assert "No alerts recorded" in html
    assert "No monitors matched this report." in html


@pytest.mark.unit
def test_generate_all_survives_one_broken_report(repository, tmp_path):
    _write_report(tmp_path / "reports", "good", "Good")
    broken = tmp_path / "reports" / "broken"
    broken.mkdir()
    (broken / "report.yaml").write_text('title: "Broken"\n')  # no template file

    generator = _generator(
        repository, str(tmp_path / "reports"), str(tmp_path / "output")
    )
    generated = generator.generate_all()

    assert [g.name for g in generated] == ["good"]
    assert os.path.isfile(tmp_path / "output" / "good" / "index.html")


@pytest.mark.unit
def test_format_duration_handles_timedeltas_and_junk():
    assert format_duration(timedelta(seconds=5)) == "5.00s"
    assert format_duration(1.5) == "1.50s"
    assert format_duration(None) == ""
    assert format_duration("n/a") == "n/a"


@pytest.mark.unit
def test_sparkline_escapes_its_stroke():
    svg = sparkline([1, 2, 3], stroke='red" onload="alert(1)')

    # the quote is escaped, so the value cannot break out of the attribute
    assert '" onload=' not in svg
    assert "&#34;" in svg or "&quot;" in svg


@pytest.mark.unit
def test_sparkline_needs_at_least_two_numbers():
    assert "polyline" not in sparkline([])
    assert "polyline" not in sparkline([1])
    assert "polyline" in sparkline([1, 5, 3])


@pytest.mark.unit
def test_sparkline_ignores_non_numeric_values():
    svg = sparkline([1, "n/a", 3, None])

    assert "polyline" in svg
