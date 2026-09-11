import os
from email.utils import getaddresses

import pytest

from locallib.contacts import (
    Contact,
    InvalidContactError,
    parse_contact,
    parse_contact_list,
)
from locallib.monitor_models import ContactConfig

"""
Test: contact list parsing and validation
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("email:shane@netshane.com", Contact("email", "shane@netshane.com")),
        (
            "email:Shane Mayer:shane@netshane.com",
            Contact("email", "shane@netshane.com", "Shane Mayer"),
        ),
        (" EMAIL: shane@netshane.com ", Contact("email", "shane@netshane.com")),
        ("slack:#channel", Contact("slack", "#channel")),
        ("slack:@person", Contact("slack", "@person")),
        ("teams:#channel", Contact("teams", "#channel")),
        ("teams:@person", Contact("teams", "@person")),
        ("text:+14155551212", Contact("text", "+14155551212")),
        ("text:+1 415-555-1212", Contact("text", "+1 415-555-1212")),
        ("text:(415) 555-1212", Contact("text", "(415) 555-1212")),
    ],
)
def test_valid_contacts_are_parsed(value, expected):
    assert parse_contact(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "shane@netshane.com",  # no channel prefix
        "sms:+15551234567",  # unknown channel
        "email:not-an-address",
        "email:shane@localhost",
        "email:Shane:",
        "slack:channel",  # missing # or @
        "teams:person",
        "slack:#",
        "text:",
        "text:not-a-number",
        "text:12345",  # too short
        "text:1234567890123456",  # too long
        "",
        42,
    ],
)
def test_invalid_contacts_are_rejected(value):
    with pytest.raises(InvalidContactError):
        parse_contact(value)


@pytest.mark.unit
def test_the_invalid_contact_is_named_in_the_error():
    with pytest.raises(InvalidContactError) as error:
        parse_contact_list(
            ["email:ops@example.com", "slack:oops"], field="contact.alert"
        )

    assert "slack:oops" in str(error.value)
    assert "contact.alert" in str(error.value)


@pytest.mark.unit
def test_contact_lists_keep_order_and_drop_duplicates():
    contacts = parse_contact_list(["slack:#a", "email:ops@example.com", "slack:#a"])

    assert [str(c) for c in contacts] == ["slack:#a", "email:ops@example.com"]


@pytest.mark.unit
def test_email_contacts_render_a_display_name():
    contact = parse_contact("email:Ops Team:ops@example.com")

    assert contact.address == "Ops Team <ops@example.com>"


@pytest.mark.unit
def test_a_display_name_holding_a_comma_is_quoted():
    """An unquoted comma would split the To header into a bogus recipient."""
    contact = parse_contact("email:Ops, Night:ops@example.com")

    assert contact.address == '"Ops, Night" <ops@example.com>'
    assert getaddresses([contact.address]) == [("Ops, Night", "ops@example.com")]


@pytest.mark.unit
def test_file_contacts_are_parsed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    contact = parse_contact("file:output/report.txt")

    assert contact == Contact("file", "output/report.txt")
    assert os.path.isfile("output/report.txt")


@pytest.mark.unit
def test_file_append_contacts_are_parsed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    contact = parse_contact("file_append:output/report.txt")

    assert contact == Contact("file_append", "output/report.txt")
    assert os.path.isfile("output/report.txt")


@pytest.mark.unit
@pytest.mark.parametrize("channel", ["file", "file_append"])
def test_file_validation_does_not_clobber_existing_content(
    channel, tmp_path, monkeypatch
):
    """Definitions load far more often than they notify, so validating a
    'file' contact must not truncate a target that already holds a previous
    report - only an actual delivery may overwrite it.
    """
    monkeypatch.chdir(tmp_path)
    os.makedirs("output")
    with open("output/report.txt", "w") as f:
        f.write("existing content")

    parse_contact(f"{channel}:output/report.txt")

    with open("output/report.txt") as f:
        assert f.read() == "existing content"


@pytest.mark.unit
def test_a_file_contact_with_an_empty_path_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(InvalidContactError):
        parse_contact("file:")


@pytest.mark.unit
def test_a_file_contact_with_an_unwritable_path_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("output")
    os.chmod("output", 0o500)

    try:
        with pytest.raises(InvalidContactError):
            parse_contact("file:output/report.txt")
    finally:
        os.chmod("output", 0o700)


@pytest.mark.unit
def test_contact_config_reads_every_contact_type():
    config = ContactConfig.from_dict(
        {
            "alert": ["email:a@example.com"],
            "report": ["slack:#reports"],
            "info": ["teams:@shane"],
            "notify": [],
            "error": ["email:Errors:e@example.com"],
            "combine_alerts": False,
        }
    )

    assert config.for_type("alert") == [Contact("email", "a@example.com")]
    assert config.for_type("report") == [Contact("slack", "#reports")]
    assert config.for_type("info") == [Contact("teams", "@shane")]
    assert config.for_type("notify") == []
    assert config.for_type("error")[0].name == "Errors"
    assert config.combine_alerts is False
    assert config.combine_reports is True


@pytest.mark.unit
def test_an_empty_contact_section_has_no_contacts():
    config = ContactConfig.from_dict({})

    assert all(config.for_type(t) == [] for t in ("alert", "report", "info", "notify"))


@pytest.mark.unit
def test_unknown_contact_type_is_rejected():
    with pytest.raises(ValueError):
        ContactConfig().for_type("pager")


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,replacement",
    [
        ("alert_email", "alert"),
        ("report_email", "report"),
        ("combine_alert_emails", "combine_alerts"),
        ("combine_report_emails", "combine_reports"),
    ],
)
def test_removed_email_settings_are_reported(key, replacement):
    with pytest.raises(ValueError) as error:
        ContactConfig.from_dict({key: "ops@example.com"})

    assert key in str(error.value)
    assert replacement in str(error.value)
