import pytest

from locallib.chat_sender import LoggingChatSender
from locallib.contacts import parse_contact_list
from locallib.email_sender import LoggingEmailSender, SendOutcome
from locallib.file_sender import LoggingFileSender
from locallib.message_templates import MessageTemplateFactory
from locallib.text_sender import LoggingTextSender
from locallib.monitor_models import ContactConfig, MonitorDefinition
from locallib.monitor_results import (
    Alert,
    MonitorResult,
    Report,
    ResultStatus,
    TestResult,
)
from locallib.notifier import Notifier

"""
Test: alert / report / info routing across email, slack, and teams contacts
"""


@pytest.fixture()
def sender():
    return LoggingEmailSender()


@pytest.fixture()
def slack():
    return LoggingChatSender("slack")


@pytest.fixture()
def teams():
    return LoggingChatSender("teams")


@pytest.fixture()
def file_sender():
    return LoggingFileSender(append=False)


@pytest.fixture()
def file_append_sender():
    return LoggingFileSender(append=True)


@pytest.fixture()
def text_sender():
    return LoggingTextSender(provider="imsg")


@pytest.fixture()
def notifier(sender, slack, teams, file_sender, file_append_sender, text_sender):
    return Notifier(
        email_sender=sender,
        chat_senders={"slack": slack, "teams": teams},
        file_senders={"file": file_sender, "file_append": file_append_sender},
        text_sender=text_sender,
    )


def _definition(name: str = "xyz", **contact) -> MonitorDefinition:
    lists = {
        key: parse_contact_list(value)
        for key, value in contact.items()
        if not key.startswith("combine_")
    }
    flags = {k: v for k, v in contact.items() if k.startswith("combine_")}

    return MonitorDefinition(
        name=name,
        source_file=f"{name}.toml",
        description=f"{name} description",
        monitor_type_alert=True,
        monitor_type_report=True,
        contact=ContactConfig(**lists, **flags),
    )


def _alert_result(name: str = "xyz", count: int = 1) -> MonitorResult:
    return MonitorResult(
        monitor_name=name,
        status=ResultStatus.ALERT,
        test_results=[
            TestResult(
                key=f"{name}.test",
                test_type="ping",
                status=ResultStatus.ALERT,
                alerts=[
                    Alert(name=f"host{i}", message=f"host{i} is down")
                    for i in range(count)
                ],
            )
        ],
    )


def _report_result(name: str = "xyz") -> MonitorResult:
    return MonitorResult(
        monitor_name=name,
        test_results=[
            TestResult(
                key=f"{name}.report",
                test_type="dbreport",
                message="12 rows checked",
                reports=[Report(title="rows", columns=["Name"], rows=[["a"], ["b"]])],
            )
        ],
    )


@pytest.mark.unit
def test_alerts_are_combined_into_one_email(notifier, sender):
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=True)

    notifier.notify(definition, _alert_result(count=2))
    assert sender.sent == []

    notifier.flush()

    assert len(sender.sent) == 1
    assert sender.sent[0].to == ["ops@example.com"]
    assert "host0 is down" in sender.sent[0].body
    assert "host1 is down" in sender.sent[0].body


@pytest.mark.unit
def test_uncombined_alerts_send_immediately(notifier, sender):
    definition = _definition(alert=["email:ops@example.com"], combine_alerts=False)

    notifier.notify(definition, _alert_result(count=2))

    assert len(sender.sent) == 2
    notifier.flush()
    assert len(sender.sent) == 2


@pytest.mark.unit
def test_alerts_from_several_monitors_share_one_combined_email(notifier, sender):
    contacts = ["email:ops@example.com"]
    notifier.notify(_definition("a", alert=contacts), _alert_result("a"))
    notifier.notify(_definition("b", alert=contacts), _alert_result("b"))

    notifier.flush()

    assert len(sender.sent) == 1
    assert "[a]" in sender.sent[0].body
    assert "[b]" in sender.sent[0].body


@pytest.mark.unit
def test_no_alert_contacts_means_no_notification(notifier, sender):
    notifier.notify(_definition(), _alert_result())
    notifier.flush()

    assert sender.sent == []


@pytest.mark.unit
def test_alert_only_monitors_do_not_send_reports(notifier, sender):
    definition = _definition(report=["email:reports@example.com"])
    definition.monitor_type_report = False

    notifier.notify(definition, _report_result())
    notifier.flush()

    assert sender.sent == []


@pytest.mark.unit
def test_reports_are_combined_with_an_html_alternative(notifier, sender):
    definition = _definition(report=["email:reports@example.com"])

    notifier.notify(definition, _report_result())
    notifier.flush()

    assert len(sender.sent) == 1
    assert "rows" in sender.sent[0].body
    assert "<table" in sender.sent[0].html


@pytest.mark.unit
def test_report_notifications_include_all_test_messages(notifier, sender):
    definition = _definition(report=["email:reports@example.com"])

    notifier.notify(definition, _report_result())
    notifier.flush()

    assert "12 rows checked" in sender.sent[0].body


@pytest.mark.unit
def test_report_html_escapes_test_messages_and_titles(notifier, sender):
    definition = _definition(report=["email:reports@example.com"])
    result = _report_result()
    result.test_results[0].message = "rows <= 5 & rising"

    notifier.notify(definition, result)
    notifier.flush()

    assert "rows &lt;= 5 &amp; rising" in sender.sent[0].html
    assert "<li>rows <= 5" not in sender.sent[0].html


@pytest.mark.unit
def test_a_test_with_no_message_has_no_dangling_separator(notifier, sender):
    definition = _definition(report=["email:reports@example.com"])
    result = _report_result()
    result.test_results[0].message = None

    notifier.notify(definition, result)
    notifier.flush()

    assert "xyz.report (dbreport): ok" in sender.sent[0].body
    assert " - \n" not in sender.sent[0].body


@pytest.mark.unit
def test_uncombined_reports_send_immediately(notifier, sender):
    definition = _definition(
        report=["email:reports@example.com"], combine_reports=False
    )

    notifier.notify(definition, _report_result())

    assert len(sender.sent) == 1


@pytest.mark.unit
def test_error_notification_is_sent_for_failed_tests(notifier, sender):
    definition = _definition(error=["email:errors@example.com"])
    result = MonitorResult(
        monitor_name="xyz",
        status=ResultStatus.ERROR,
        test_results=[
            TestResult(
                key="xyz.db",
                test_type="dbflag",
                status=ResultStatus.ERROR,
                error="connection refused",
            )
        ],
    )

    notifier.notify(definition, result)

    assert len(sender.sent) == 1
    assert "connection refused" in sender.sent[0].body


@pytest.mark.unit
def test_info_and_notify_contacts_are_used_on_every_run(notifier, sender):
    definition = _definition(
        info=["email:info@example.com"], notify=["email:ran@example.com"]
    )

    notifier.notify(definition, _report_result())

    subjects = [s.subject for s in sender.sent]
    assert any("info:" in s for s in subjects)
    assert any("ran:" in s for s in subjects)


@pytest.mark.unit
def test_several_email_contacts_become_multiple_recipients(notifier, sender):
    definition = _definition(
        alert=["email:a@example.com", "email:Bee:b@example.com"],
        combine_alerts=False,
    )

    notifier.notify(definition, _alert_result())

    assert sender.sent[0].to == ["a@example.com", "Bee <b@example.com>"]


@pytest.mark.unit
def test_slack_and_teams_contacts_are_notified(notifier, sender, slack, teams):
    definition = _definition(
        alert=["email:ops@example.com", "slack:#alerts", "teams:@shane"],
    )

    notifier.notify(definition, _alert_result())
    notifier.flush()

    assert len(sender.sent) == 1
    assert [m.target for m in slack.sent] == ["#alerts"]
    assert [m.target for m in teams.sent] == ["@shane"]
    assert "host0 is down" in slack.sent[0].body


@pytest.mark.unit
def test_file_and_file_append_contacts_are_notified(
    notifier, sender, file_sender, file_append_sender
):
    definition = _definition(
        alert=[
            "email:ops@example.com",
            "file:output/report.txt",
            "file_append:output/log.txt",
        ],
    )

    notifier.notify(definition, _alert_result())
    notifier.flush()

    assert len(sender.sent) == 1
    assert [m.target for m in file_sender.sent] == ["output/report.txt"]
    assert [m.target for m in file_append_sender.sent] == ["output/log.txt"]
    assert "host0 is down" in file_sender.sent[0].body


@pytest.mark.unit
def test_text_contacts_are_notified(notifier, sender, text_sender):
    definition = _definition(
        alert=["email:ops@example.com", "text:+14155551212"],
    )

    notifier.notify(definition, _alert_result())
    notifier.flush()

    assert len(sender.sent) == 1
    assert [m.target for m in text_sender.sent] == ["+14155551212"]
    assert "host0 is down" in text_sender.sent[0].body


@pytest.mark.unit
def test_an_unconfigured_text_sender_is_skipped(sender):
    notifier = Notifier(email_sender=sender)
    definition = _definition(
        alert=["email:ops@example.com", "text:+14155551212"], combine_alerts=False
    )

    notifier.notify(definition, _alert_result())

    assert len(sender.sent) == 1


@pytest.mark.unit
def test_report_html_is_passed_through_to_the_file_sender(notifier, file_sender):
    definition = _definition(report=["file:output/report.html"])

    notifier.notify(definition, _report_result())
    notifier.flush()

    assert file_sender.sent[0].html is not None
    assert "<table" in file_sender.sent[0].html


@pytest.mark.unit
def test_each_contact_gets_its_own_combined_message(notifier, slack):
    contacts = ["slack:#alerts", "slack:@shane"]

    notifier.notify(_definition("a", alert=contacts), _alert_result("a"))
    notifier.notify(_definition("b", alert=contacts), _alert_result("b"))
    notifier.flush()

    assert sorted(m.target for m in slack.sent) == ["#alerts", "@shane"]
    for message in slack.sent:
        assert "[a]" in message.body and "[b]" in message.body


@pytest.mark.unit
def test_a_failing_sender_does_not_break_the_run(sender, slack):
    class Broken(LoggingEmailSender):
        def send(self, to, subject, body, html=None):
            raise RuntimeError("smtp down")

    notifier = Notifier(email_sender=Broken(), chat_senders={"slack": slack})
    definition = _definition(
        alert=["email:ops@example.com", "slack:#alerts"], combine_alerts=False
    )

    notifier.notify(definition, _alert_result())
    notifier.flush()

    # the email failed but slack was still notified
    assert len(slack.sent) == 1


@pytest.mark.unit
def test_a_failing_chat_target_does_not_stop_the_others():
    class Broken(LoggingChatSender):
        def send(self, target, subject, body):
            if target == "#alerts":
                raise RuntimeError("slack down")
            super().send(target, subject, body)

    slack = Broken("slack")
    notifier = Notifier(
        email_sender=LoggingEmailSender(), chat_senders={"slack": slack}
    )
    definition = _definition(
        alert=["slack:#alerts", "slack:@shane"], combine_alerts=False
    )

    notifier.notify(definition, _alert_result())

    assert [m.target for m in slack.sent] == ["@shane"]


@pytest.mark.unit
def test_an_unconfigured_channel_is_skipped(sender):
    notifier = Notifier(email_sender=sender)
    definition = _definition(
        alert=["email:ops@example.com", "slack:#alerts"], combine_alerts=False
    )

    notifier.notify(definition, _alert_result())

    assert len(sender.sent) == 1


@pytest.mark.unit
def test_subject_prefix_is_applied(sender, slack):
    notifier = Notifier(
        email_sender=sender,
        chat_senders={"slack": slack},
        subject_prefix="[monmon]",
    )
    definition = _definition(notify=["email:ran@example.com", "slack:#ops"])

    notifier.notify(definition, _report_result())

    assert sender.sent[0].subject.startswith("[monmon] ")
    assert slack.sent[0].subject.startswith("[monmon] ")


@pytest.mark.unit
def test_send_test_notifies_the_named_contact_list(notifier, sender, slack):
    definition = _definition(
        alert=["email:ops@example.com", "slack:#alerts"],
        notify=["email:ran@example.com"],
    )

    deliveries = notifier.send_test(definition, "alert")

    assert len(deliveries) == 2
    assert len(sender.sent) == 1
    assert len(slack.sent) == 1
    assert "test:" in sender.sent[0].subject


@pytest.mark.unit
def test_send_test_with_no_contacts_sends_nothing(notifier, sender):
    assert notifier.send_test(_definition(), "alert") == []
    assert sender.sent == []


@pytest.mark.unit
def test_send_test_reports_logging_senders_as_logged_not_sent(notifier):
    definition = _definition(alert=["email:ops@example.com", "slack:#alerts"])

    deliveries = notifier.send_test(definition, "alert")

    assert all(d.sent for d in deliveries)
    assert [d.status for d in deliveries] == ["logged", "logged"]


@pytest.mark.unit
def test_send_test_reports_a_failed_delivery(slack):
    class Broken(LoggingEmailSender):
        is_live = True

        def send(self, to, subject, body, html=None):
            raise RuntimeError("smtp down")

    notifier = Notifier(email_sender=Broken(), chat_senders={"slack": slack})
    definition = _definition(alert=["email:ops@example.com", "slack:#alerts"])

    deliveries = notifier.send_test(definition, "alert")
    by_channel = {d.contact.channel: d for d in deliveries}

    assert by_channel["email"].sent is False
    assert "smtp down" in by_channel["email"].status
    assert by_channel["slack"].sent is True


@pytest.mark.unit
def test_send_test_reports_an_unconfigured_channel_as_failed(sender):
    notifier = Notifier(email_sender=sender)
    definition = _definition(alert=["slack:#alerts"])

    deliveries = notifier.send_test(definition, "alert")

    assert deliveries[0].sent is False
    assert "slack" in deliveries[0].status


@pytest.mark.unit
def test_send_test_reports_an_unconfigured_text_sender_as_failed(sender):
    notifier = Notifier(email_sender=sender)
    definition = _definition(alert=["text:+14155551212"])

    deliveries = notifier.send_test(definition, "alert")

    assert deliveries[0].sent is False


@pytest.mark.unit
def test_a_live_sender_is_reported_as_sent(slack):
    class Live(LoggingEmailSender):
        is_live = True

    notifier = Notifier(email_sender=Live())
    definition = _definition(alert=["email:ops@example.com"])

    assert notifier.send_test(definition, "alert")[0].status == "sent"


"""
Test: [contact.message] templates driving the rendered notifications
"""


def _templated(templates: dict, **kwargs) -> MonitorDefinition:
    """A definition whose message templates come from `templates`."""
    definition = _definition(**kwargs)
    definition.message_templates = MessageTemplateFactory().build(templates)

    return definition


@pytest.mark.unit
def test_a_monitor_template_renders_the_alert_message(notifier, sender):
    definition = _templated(
        {
            "alert": {
                "email": {
                    "subject": "down: {{ name }}",
                    "body": "{{ alert.message }} / {{ contact_channel }}",
                }
            }
        },
        alert=["email:ops@example.com"],
        combine_alerts=False,
    )

    notifier.notify(definition, _alert_result())

    assert sender.sent[0].subject == "[monitor] down: xyz"
    assert sender.sent[0].body == "host0 is down / email"


@pytest.mark.unit
def test_a_template_gets_the_monitor_and_contact_variables(notifier, sender):
    definition = _templated(
        {
            "notify": {
                "email": {
                    "body": "{{ name }}|{{ description }}|{{ link }}|"
                    "{{ tags | join('+') }}|{{ status }}|{{ contact_type }}|"
                    "{{ contact.target }}|{{ test_results | length }}"
                }
            }
        },
        notify=["email:ran@example.com"],
    )
    definition.link = "https://example.com/docs"
    definition.tags = ["nightly", "batch"]

    notifier.notify(definition, _report_result())

    assert sender.sent[0].body == (
        "xyz|xyz description|https://example.com/docs|nightly+batch|ok|notify|"
        "ran@example.com|1"
    )


@pytest.mark.unit
def test_each_channel_uses_its_own_template(notifier, sender, slack):
    definition = _templated(
        {
            "alert": {
                "email": {"body": "email: {{ alert.message }}"},
                "slack": {"message": "slack: {{ alert.message }}"},
            }
        },
        alert=["email:ops@example.com", "slack:#alerts"],
        combine_alerts=False,
    )

    notifier.notify(definition, _alert_result())

    assert sender.sent[0].body == "email: host0 is down"
    assert slack.sent[0].body == "slack: host0 is down"


@pytest.mark.unit
def test_a_template_that_fails_to_render_does_not_stop_the_other_contacts(
    notifier, sender, slack
):
    definition = _templated(
        {"alert": {"email": {"body": "{{ 1 / 0 }}"}}},
        alert=["email:ops@example.com", "slack:#alerts"],
        combine_alerts=False,
    )

    notifier.notify(definition, _alert_result())

    assert sender.sent == []
    assert len(slack.sent) == 1


@pytest.mark.unit
def test_a_template_that_renders_empty_sends_nothing(notifier, sender):
    definition = _templated(
        {"notify": {"email": {"body": "{% if false %}never{% endif %}"}}},
        notify=["email:ran@example.com"],
    )

    notifier.notify(definition, _report_result())

    assert sender.sent == []


@pytest.mark.unit
def test_the_batch_subject_is_used_for_a_combined_message(sender):
    notifier = Notifier(
        email_sender=sender,
        templates=MessageTemplateFactory(
            {
                "alert": {
                    "email": {
                        "batch_subject": "{{ alert_count }}/{{ monitor_count }} "
                        "({{ report_count }}): {{ monitor_names | join('+') }} "
                        "-> {{ contact.target }} {{ contact_type }}/{{ contact_channel }}"
                    }
                }
            }
        ).build(),
    )
    contacts = ["email:ops@example.com"]

    notifier.notify(_definition("a", alert=contacts), _alert_result("a"))
    notifier.notify(_definition("b", alert=contacts), _alert_result("b"))
    notifier.flush()

    assert sender.sent[0].subject == (
        "[monitor] 2/2 (0): a+b -> ops@example.com alert/email"
    )


@pytest.mark.unit
def test_an_empty_batch_subject_falls_back_to_the_first_monitors_subject(sender):
    notifier = Notifier(
        email_sender=sender,
        templates=MessageTemplateFactory(
            {"alert": {"email": {"batch_subject": "{% if false %}x{% endif %}"}}}
        ).build(),
    )

    notifier.notify(
        _definition("a", alert=["email:ops@example.com"]), _alert_result("a")
    )
    notifier.flush()

    assert sender.sent[0].subject == "[monitor] alert: a - host0"


@pytest.mark.unit
def test_combined_fragments_are_rendered_eagerly_one_per_alert(sender):
    notifier = Notifier(
        email_sender=sender,
        templates=MessageTemplateFactory(
            {"alert": {"email": {"body": "<{{ name }}:{{ alert.name }}>"}}}
        ).build(),
    )
    definition = _definition("a", alert=["email:ops@example.com"])
    definition.message_templates = notifier.templates

    notifier.notify(definition, _alert_result("a", count=2))
    # the monitor's alerts are already rendered before the flush
    assert notifier._pending[list(notifier._pending)[0]].alerts[0].message.body == (
        "<a:host0>"
    )

    notifier.flush()

    assert sender.sent[0].body == "<a:host0>\n\n<a:host1>"


@pytest.mark.unit
def test_combined_report_html_fragments_are_merged(notifier, sender):
    contacts = ["email:reports@example.com"]

    notifier.notify(_definition("a", report=contacts), _report_result("a"))
    notifier.notify(_definition("b", report=contacts), _report_result("b"))
    notifier.flush()

    assert len(sender.sent) == 1
    assert sender.sent[0].html.count("<table") == 2
    assert "a - rows" in sender.sent[0].html
    assert "b - rows" in sender.sent[0].html


@pytest.mark.unit
def test_contacts_sent_different_messages_get_separate_emails(sender):
    notifier = Notifier(
        email_sender=sender,
        templates=MessageTemplateFactory(
            {"notify": {"email": {"body": "hello {{ contact.target }}"}}}
        ).build(),
    )
    definition = _definition(notify=["email:a@example.com", "email:b@example.com"])

    notifier.notify(definition, _report_result())

    assert [s.to for s in sender.sent] == [["a@example.com"], ["b@example.com"]]
    assert sender.sent[0].body == "hello a@example.com"


@pytest.mark.unit
def test_send_test_renders_the_contact_types_template_with_placeholders(
    notifier, sender
):
    definition = _templated(
        {"alert": {"email": {"body": "{{ alert.message }} / {{ alert.value }}"}}},
        alert=["email:ops@example.com"],
    )

    notifier.send_test(definition, "alert")

    assert sender.sent[0].subject.startswith("[monitor] test: alert: xyz")
    assert "sample alert message" in sender.sent[0].body
    assert "42" in sender.sent[0].body


@pytest.mark.unit
def test_send_test_uses_the_report_template_for_report_contacts(notifier, sender):
    definition = _templated(
        {"report": {"email": {"body": "{{ reports[0].title }}"}}},
        report=["email:r@example.com"],
    )

    notifier.send_test(definition, "report")

    assert sender.sent[0].body == "Sample report"


@pytest.mark.unit
def test_an_undefined_variable_does_not_lose_the_message(notifier, sender):
    definition = _templated(
        {"notify": {"email": {"body": "value: {{ missing.thing }}"}}},
        notify=["email:ran@example.com"],
    )

    notifier.notify(definition, _report_result())

    assert sender.sent[0].body == "value: (Undefined)"


@pytest.mark.unit
def test_a_long_body_is_truncated_to_the_channel_limit(sender):
    notifier = Notifier(
        email_sender=sender,
        templates=MessageTemplateFactory(
            {"notify": {"email": {"body": "{{ name }}"}}}, body_limits={"email": 6}
        ).build(),
    )
    definition = _definition("a-very-long-monitor-name", notify=["email:r@example.com"])

    notifier.notify(definition, _report_result("a-very-long-monitor-name"))

    assert sender.sent[0].body == "a-v..."


class ThrottlingEmailSender(LoggingEmailSender):
    """Stands in for the quota limited sender - drops the named addresses."""

    is_live = True

    def __init__(self, throttled: list[str]):
        super().__init__()
        self.throttled = throttled

    def send(self, to: list[str], subject: str, body: str, html: str | None = None):
        allowed = [address for address in to if address not in self.throttled]
        if allowed:
            super().send(allowed, subject, body, html)

        return SendOutcome(
            throttled=[address for address in to if address in self.throttled]
        )


@pytest.mark.unit
def test_a_throttled_recipient_is_reported_without_failing_the_run():
    sender = ThrottlingEmailSender(throttled=["b@x.com"])
    notifier = Notifier(email_sender=sender)
    definition = _definition(alert="email:a@x.com, email:b@x.com")

    deliveries = notifier.send_test(definition, "alert")

    by_address = {d.contact.address: d for d in deliveries}
    assert by_address["a@x.com"].status == "sent"
    assert by_address["b@x.com"].status == "throttled"
    # a throttled delivery is not an error
    assert by_address["b@x.com"].error is None
    assert by_address["b@x.com"].sent
    assert sender.sent[0].to == ["a@x.com"]


@pytest.mark.unit
def test_a_sender_that_reports_nothing_means_everything_was_sent(notifier, sender):
    definition = _definition(alert="email:a@x.com")

    deliveries = notifier.send_test(definition, "alert")

    assert [d.throttled for d in deliveries] == [False]
    assert deliveries[0].status == "logged"
