import pytest

from locallib.contacts import Contact
from locallib.message_templates import (
    UNDEFINED_TEXT,
    MessageTemplateError,
    MessageTemplateFactory,
)

"""
Test: [contact.message] template resolution, compilation, and rendering
"""


def _context(**overrides) -> dict:
    context = {
        "name": "xyz",
        "description": "the xyz thing",
        "link": "https://example.com/support",
        "tags": ["nightly"],
        "reports": [],
        "test_results": [],
        "started_at": None,
        "duration_seconds": 1.5,
        "status": "alert",
        "contact": Contact(channel="email", target="ops@example.com"),
        "contact_type": "alert",
        "contact_channel": "email",
    }
    context.update(overrides)

    return context


@pytest.mark.unit
def test_every_contact_type_and_channel_has_a_default_template():
    from locallib.monitor_results import Alert, Report, ResultStatus, TestResult

    templates = MessageTemplateFactory().build()
    context = _context(
        alert=Alert(message="host is down", name="host"),
        reports=[Report(title="rows", columns=["Name"], rows=[["a"]])],
        test_results=[
            TestResult(
                key="xyz.ping",
                test_type="ping",
                status=ResultStatus.ERROR,
                error="boom",
            )
        ],
    )

    for contact_type in ("alert", "report", "info", "notify", "error"):
        for channel in ("email", "slack", "teams", "file", "file_append"):
            message = templates.render(contact_type, channel, context)

            assert message.subject
            assert message.body


@pytest.mark.unit
def test_only_email_and_file_report_templates_carry_an_html_alternative():
    templates = MessageTemplateFactory().build()

    assert templates.has_html("report", "email") is True
    assert templates.has_html("report", "file") is True
    assert templates.has_html("report", "file_append") is True
    assert templates.has_html("report", "slack") is False
    assert templates.has_html("alert", "email") is False


@pytest.mark.unit
def test_an_undefined_variable_renders_as_undefined():
    factory = MessageTemplateFactory(
        {"alert": {"email": {"subject": "{{ nope }} / {{ nope.deeper }}"}}}
    )

    message = factory.build().render("alert", "email", _context())

    assert message.subject == f"{UNDEFINED_TEXT} / {UNDEFINED_TEXT}"


@pytest.mark.unit
def test_a_monitor_override_keeps_the_fields_it_does_not_set():
    factory = MessageTemplateFactory(
        {"alert": {"email": {"subject": "settings subject", "body": "settings body"}}}
    )

    message = factory.build({"alert": {"email": {"body": "monitor body"}}}).render(
        "alert", "email", _context()
    )

    assert message.subject == "settings subject"
    assert message.body == "monitor body"


@pytest.mark.unit
def test_settings_templates_override_the_built_in_defaults():
    factory = MessageTemplateFactory(
        {"notify": {"slack": {"message": "ran {{ name }}"}}}
    )

    assert factory.build().render("notify", "slack", _context()).body == "ran xyz"


@pytest.mark.unit
def test_an_empty_template_string_inherits_rather_than_blanking():
    factory = MessageTemplateFactory({"notify": {"email": {"subject": "  "}}})

    assert factory.build().render("notify", "email", _context()).subject == (
        "ran: xyz (alert)"
    )


@pytest.mark.unit
def test_message_is_an_alias_for_body():
    factory = MessageTemplateFactory()

    templates = factory.build({"alert": {"teams": {"message": "hi {{ name }}"}}})

    assert templates.render("alert", "teams", _context()).body == "hi xyz"


@pytest.mark.unit
def test_a_plain_text_email_body_is_not_escaped():
    factory = MessageTemplateFactory()
    templates = factory.build({"alert": {"email": {"body": "{{ description }}"}}})

    body = templates.render(
        "alert", "email", _context(description="rows <= 5 & rising")
    ).body

    assert body == "rows <= 5 & rising"


@pytest.mark.unit
def test_html_and_chat_bodies_escape_their_variables():
    factory = MessageTemplateFactory()
    templates = factory.build(
        {
            "alert": {
                "email": {"html": "<p>{{ description }}</p>"},
                "slack": {"message": "{{ description }}"},
            }
        }
    )
    context = _context(description="rows <= 5 & rising")

    assert templates.render("alert", "email", context).html == (
        "<p>rows &lt;= 5 &amp; rising</p>"
    )
    assert templates.render("alert", "slack", context).body == (
        "rows &lt;= 5 &amp; rising"
    )


@pytest.mark.unit
def test_a_plain_text_file_body_is_not_escaped():
    factory = MessageTemplateFactory()
    templates = factory.build({"alert": {"file": {"body": "{{ description }}"}}})

    body = templates.render(
        "alert", "file", _context(description="rows <= 5 & rising")
    ).body

    assert body == "rows <= 5 & rising"


@pytest.mark.unit
def test_a_file_html_alternative_is_available():
    factory = MessageTemplateFactory()
    templates = factory.build({"alert": {"file": {"html": "<p>{{ description }}</p>"}}})

    message = templates.render(
        "alert", "file", _context(description="rows <= 5 & rising")
    )

    assert message.html == "<p>rows &lt;= 5 &amp; rising</p>"


@pytest.mark.unit
def test_a_batch_subject_renders_from_the_batch_variables():
    factory = MessageTemplateFactory(
        {
            "alert": {
                "email": {
                    "batch_subject": "{{ alert_count }} of {{ monitor_count }}: "
                    "{{ monitor_names | join('+') }}"
                }
            }
        }
    )

    subject = factory.build().render_batch_subject(
        "alert",
        "email",
        {"alert_count": 2, "monitor_count": 3, "monitor_names": ["a", "b", "c"]},
    )

    assert subject == "2 of 3: a+b+c"


@pytest.mark.unit
def test_batch_subject_may_not_be_set_on_a_monitor():
    factory = MessageTemplateFactory()

    with pytest.raises(MessageTemplateError, match="batch_subject"):
        factory.build({"alert": {"email": {"batch_subject": "nope"}}})


@pytest.mark.unit
def test_an_over_long_subject_is_truncated_with_an_ellipsis():
    factory = MessageTemplateFactory(
        {"alert": {"email": {"subject": "{{ description }}"}}}, subject_limit=10
    )

    subject = factory.build().render("alert", "email", _context(description="x" * 50))

    assert subject.subject == "xxxxxxx..."
    assert len(subject.subject) == 10


@pytest.mark.unit
def test_an_over_long_body_is_truncated_per_channel():
    factory = MessageTemplateFactory(
        {"alert": {"slack": {"message": "{{ description }}"}}},
        body_limits={"slack": 8},
    )

    message = factory.build().render("alert", "slack", _context(description="y" * 50))

    assert message.body == "yyyyy..."


@pytest.mark.unit
def test_a_template_that_will_not_compile_is_rejected_when_built():
    factory = MessageTemplateFactory()

    with pytest.raises(MessageTemplateError, match="Invalid 'body' template"):
        factory.build({"alert": {"email": {"body": "{% for x in %}"}}})


@pytest.mark.unit
def test_a_template_that_fails_at_render_time_raises():
    factory = MessageTemplateFactory()
    templates = factory.build({"alert": {"email": {"body": "{{ 1 / 0 }}"}}})

    with pytest.raises(MessageTemplateError, match="Failed to render"):
        templates.render("alert", "email", _context())


@pytest.mark.unit
@pytest.mark.parametrize(
    "section,expected",
    [
        ({"nope": {"email": {"body": "x"}}}, "unknown message template type"),
        ({"alert": {"carrier": {"body": "x"}}}, "unknown message template channel"),
        ({"alert": {"email": {"bodyy": "x"}}}, "unknown setting 'bodyy'"),
        ({"alert": {"slack": {"html": "x"}}}, "only supported on the .*channels"),
        ({"alert": {"email": {"body": 5}}}, "must be a template string"),
        ({"alert": "not a table"}, "must hold one"),
        ({"alert": {"email": "not a table"}}, "must be a table of template strings"),
    ],
)
def test_a_malformed_message_section_is_rejected(section, expected):
    with pytest.raises(MessageTemplateError, match=expected):
        MessageTemplateFactory().build(section)


@pytest.mark.unit
def test_a_report_can_be_rendered_as_a_text_or_html_table():
    from locallib.monitor_results import Report

    factory = MessageTemplateFactory()
    templates = factory.build(
        {
            "report": {
                "email": {
                    "body": "{{ reports[0] | table }}",
                    "html": "{{ reports[0] | html_table }}",
                }
            }
        }
    )
    report = Report(title="rows", columns=["Name"], rows=[["a <b>"]])

    message = templates.render("report", "email", _context(reports=[report]))

    assert "a <b>" in message.body
    assert "<table" in message.html
    assert "a &lt;b&gt;" in message.html
