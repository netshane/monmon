from datetime import datetime, timedelta

import pytest

from locallib.report_models import (
    FieldSpec,
    MonitorSelector,
    ReportConfig,
    ReportConfigError,
    parse_duration,
    parse_timestamp,
)

"""
Test: report definition models

Covers window parsing and monitor matching used by report.yaml files.
"""

NOW = datetime(2026, 8, 12, 12, 0, 0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected",
    [
        ("30d", timedelta(days=30)),
        ("7 days", timedelta(days=7)),
        ("12h", timedelta(hours=12)),
        ("90m", timedelta(minutes=90)),
        ("2w", timedelta(weeks=2)),
        ("45", timedelta(days=45)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.unit
def test_parse_duration_rejects_nonsense():
    with pytest.raises(ReportConfigError):
        parse_duration("every other tuesday")


@pytest.mark.unit
def test_parse_timestamp_accepts_dates_and_datetimes():
    assert parse_timestamp("2026-07-01") == datetime(2026, 7, 1)
    assert parse_timestamp("2026-07-01 06:30") == datetime(2026, 7, 1, 6, 30)


@pytest.mark.unit
def test_latest_field_has_no_window():
    spec = FieldSpec.from_dict("status", {"mode": "latest"})

    assert spec.is_range is False
    assert spec.window(NOW) == (None, None)


@pytest.mark.unit
def test_range_field_resolves_relative_window():
    spec = FieldSpec.from_dict("run_time", {"mode": "range", "last": "30d"})

    since, until = spec.window(NOW)

    assert since == NOW - timedelta(days=30)
    assert until is None


@pytest.mark.unit
def test_range_field_uses_explicit_since():
    spec = FieldSpec.from_dict("errors", {"mode": "range", "since": "2026-07-01"})

    assert spec.window(NOW) == (datetime(2026, 7, 1), None)


@pytest.mark.unit
def test_unknown_mode_is_rejected():
    with pytest.raises(ReportConfigError):
        FieldSpec.from_dict("status", {"mode": "average"})


@pytest.mark.unit
def test_selector_matches_glob_tag_and_exclude():
    selector = MonitorSelector.from_dict(
        {"match": "backup-*", "tags": ["nightly"], "exclude": ["backup-old"]}
    )

    assert selector.matches("backup-db") is True
    assert selector.matches("web-front", ["nightly"]) is True
    assert selector.matches("web-front", ["daily"]) is False
    assert selector.matches("backup-old") is False


@pytest.mark.unit
def test_selector_with_no_criteria_matches_everything():
    selector = MonitorSelector.from_dict({"fields": {"status": {"mode": "latest"}}})

    assert selector.matches("anything") is True
    assert selector.fields["status"].mode == "latest"


@pytest.mark.unit
def test_sections_default_to_enabled_and_a_list_disables_the_rest():
    config = ReportConfig.from_dict("r", "/tmp/r", {"title": "t"})
    assert config.section_enabled("errors") is True

    listed = ReportConfig.from_dict("r", "/tmp/r", {"sections": ["summary"]})
    assert listed.section_enabled("summary") is True
    assert listed.section_enabled("errors") is False


@pytest.mark.unit
def test_sections_mapping_only_turns_off_what_it_names():
    config = ReportConfig.from_dict("r", "/tmp/r", {"sections": {"alerts": False}})

    assert config.section_enabled("alerts") is False
    assert config.section_enabled("summary") is True


@pytest.mark.unit
def test_extra_keys_are_kept_for_templates():
    config = ReportConfig.from_dict("r", "/tmp/r", {"title": "t", "footer": "hello"})

    assert config.extra == {"footer": "hello"}
