from datetime import datetime, timezone

import pytest
from jinja2.exceptions import TemplateSyntaxError, UndefinedError

from locallib.value_expander import ValueExpander

"""
Test: Jinja value expansion
"""

NOW = datetime(2026, 8, 12, 10, 30, 15)


@pytest.fixture()
def expander():
    return ValueExpander(now_provider=lambda: NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    "template,expected",
    [
        ("{{ today }}", "2026-08-12 00:00:00"),
        ("{{ yesterday }}", "2026-08-11 00:00:00"),
        ("{{ tomorrow }}", "2026-08-13 00:00:00"),
        ("{{ today_iso_format }}", "2026-08-12T00:00:00"),
        ("{{ yesterday_iso_format }}", "2026-08-11T00:00:00"),
        ("{{ now_iso_format }}", "2026-08-12T10:30:15"),
        ("{{ epoch }}", str(int(NOW.timestamp()))),
        ("{{ epoch_ms }}", str(int(NOW.timestamp() * 1000))),
        ("{{ now | strftime('%Y/%m/%d') }}", "2026/08/12"),
        ("{{ today | date }}", "2026-08-12"),
        ("{{ (today - timedelta(days=7)) | isoformat }}", "2026-08-05T00:00:00"),
        ("{{ (now - timedelta(minutes=5)) | isoformat }}", "2026-08-12T10:25:15"),
        ("{{ (today + timedelta(days=2)) | date }}", "2026-08-14"),
    ],
)
def test_known_templates(expander, template, expected):
    assert expander.expand_text(template) == expected


@pytest.mark.unit
def test_utcnow_is_the_actual_utc_time(expander):
    before = datetime.now(timezone.utc)
    rendered = expander.expand_text("{{ utcnow_iso_format }}")
    after = datetime.now(timezone.utc)

    rendered_dt = datetime.fromisoformat(rendered)
    assert before <= rendered_dt <= after


@pytest.mark.unit
def test_templates_are_rendered_in_text(expander):
    query = '{"gte": "{{ today_iso_format }}", "lte": "{{ now_iso_format }}"}'

    assert expander.expand_text(query) == (
        '{"gte": "2026-08-12T00:00:00", "lte": "2026-08-12T10:30:15"}'
    )


@pytest.mark.unit
def test_plain_strings_pass_through(expander):
    assert expander.expand_text("value") == "value"


@pytest.mark.unit
def test_undefined_variable_raises(expander):
    with pytest.raises(UndefinedError):
        expander.expand_text("value {{ nope }}")


@pytest.mark.unit
def test_old_delimiter_syntax_raises(expander):
    with pytest.raises(TemplateSyntaxError):
        expander.expand_text("{{>today<}}")


@pytest.mark.unit
def test_lists_and_dicts_are_expanded(expander):
    expanded = expander.expand(
        {
            "args": ["123", "{{ today | date }}"],
            "count": 5,
            "nested": {"when": "{{ today | date }}"},
        }
    )

    assert expanded == {
        "args": ["123", "2026-08-12"],
        "count": 5,
        "nested": {"when": "2026-08-12"},
    }


@pytest.mark.unit
def test_non_string_values_pass_through(expander):
    assert expander.expand(4.2) == 4.2
    assert expander.expand(None) is None
