from datetime import datetime

import pytest

from locallib.value_expander import ValueExpander

"""
Test: {{>token<}} value expansion
"""

NOW = datetime(2026, 8, 12, 10, 30, 15)


@pytest.fixture()
def expander():
    return ValueExpander(now_provider=lambda: NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    "token,expected",
    [
        ("today", "2026-08-12"),
        ("yesterday", "2026-08-11"),
        ("tomorrow", "2026-08-13"),
        ("today_iso_format", "2026-08-12T00:00:00"),
        ("yesterday_iso_format", "2026-08-11T00:00:00"),
        # the sample monitor file spells it this way
        ("yesterday_io_format", "2026-08-11T00:00:00"),
        ("now_iso_format", "2026-08-12T10:30:15"),
        ("utcnow_iso_format", "2026-08-12T10:30:15"),
        ("epoch", str(int(NOW.timestamp()))),
        ("strftime:%Y/%m/%d", "2026/08/12"),
    ],
)
def test_known_tokens(expander, token, expected):
    assert expander.evaluate(token) == expected


@pytest.mark.unit
def test_offsets_are_applied(expander):
    assert expander.evaluate("today-7d") == "2026-08-05"
    assert expander.evaluate("now-5min") == "2026-08-12T10:25:15"
    assert expander.evaluate("today+2 days") == "2026-08-14"


@pytest.mark.unit
def test_tokens_are_replaced_in_text(expander):
    query = '{"gte": "{{>today_iso_format<}}", "lte": "{{> now_iso_format <}}"}'

    assert expander.expand_text(query) == (
        '{"gte": "2026-08-12T00:00:00", "lte": "2026-08-12T10:30:15"}'
    )


@pytest.mark.unit
def test_unknown_tokens_are_left_alone(expander):
    assert expander.expand_text("value {{>nope<}}") == "value {{>nope<}}"


@pytest.mark.unit
def test_lists_and_dicts_are_expanded(expander):
    expanded = expander.expand(
        {"args": ["123", "{{>today<}}"], "count": 5, "nested": {"when": "{{>today<}}"}}
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
