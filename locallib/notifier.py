"""Routes monitor alerts, reports, and notifications to a monitor's contacts.

Every message is produced by a Jinja2 template from `[contact.message]` - see
`message_templates` for how a monitor's templates are resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from .chat_sender import ChatSender
from .contacts import (
    CONTACT_TYPES,
    EMAIL_CHANNEL,
    FILE_CHANNELS,
    TEXT_CHANNEL,
    Contact,
)
from .email_sender import EmailSender
from .file_sender import FileSender
from .message_templates import (
    MessageTemplateError,
    MessageTemplateFactory,
    MessageTemplateSet,
    RenderedMessage,
)
from .monitor_models import MonitorDefinition
from .monitor_results import Alert, MonitorResult, Report, ResultStatus, TestResult
from .notify_throttle import NotifyThrottleGate
from .text_sender import TextSender


@dataclass
class Delivery:
    """What happened when one contact was notified.

    `live` is False when the message only reached a logging sender, which is
    the case under `--simulate` and when a channel has no credentials.
    `throttled` is True when the email send quota dropped the message - not an
    error, so the run does not fail over it.
    """

    contact: Contact
    live: bool
    error: str | None = None
    throttled: bool = False

    @property
    def sent(self) -> bool:
        return self.error is None

    @property
    def status(self) -> str:
        if self.error:
            return f"FAILED: {self.error}"
        if self.throttled:
            return "throttled"

        return "sent" if self.live else "logged"


@dataclass
class _Fragment:
    """One monitor's already rendered contribution to a combined message.

    `status` rides along so the renotify timer recorded when the batch is
    finally delivered carries the status the fragment was raised under.
    """

    monitor_name: str
    message: RenderedMessage
    status: str | None = None


@dataclass
class _Pending:
    """Rendered fragments held back for combined delivery."""

    alerts: list[_Fragment] = field(default_factory=list)
    reports: list[_Fragment] = field(default_factory=list)

    def monitor_names(self) -> list[str]:
        names: list[str] = []
        for fragment in self.alerts + self.reports:
            if fragment.monitor_name not in names:
                names.append(fragment.monitor_name)

        return names

    @staticmethod
    def _distinct(fragments: list[_Fragment]) -> int:
        return len({fragment.monitor_name for fragment in fragments})

    @property
    def alert_count(self) -> int:
        return self._distinct(self.alerts)

    @property
    def report_count(self) -> int:
        return self._distinct(self.reports)


class Notifier:
    """Sends the notifications a monitor's `[contact]` section asks for.

    `combine_alerts` / `combine_reports` hold messages back until `flush` is
    called so a whole scheduled run produces one message per contact.  Held
    messages are keyed by contact, so batching is per contact type and channel,
    and each fragment is rendered as it arises rather than at flush time.
    """

    def __init__(
        self,
        email_sender: EmailSender,
        chat_senders: dict[str, ChatSender] | None = None,
        file_senders: dict[str, FileSender] | None = None,
        text_sender: TextSender | None = None,
        subject_prefix: str = "[monitor]",
        templates: MessageTemplateSet | None = None,
        throttle_gate: NotifyThrottleGate | None = None,
    ):
        self.email_sender = email_sender
        self.chat_senders = chat_senders or {}
        self.file_senders = file_senders or {}
        self.text_sender = text_sender
        self.subject_prefix = subject_prefix
        # used for batch subjects, and for any definition that was built
        # without templates of its own
        self.templates = templates or MessageTemplateFactory().build()
        # None leaves every notification unthrottled, which is what
        # `--simulate` and a hand built notifier get
        self.throttle_gate = throttle_gate
        self._pending: dict[Contact, _Pending] = {}

    def notify(self, definition: MonitorDefinition, result: MonitorResult):
        """Handle every notification arising from one monitor result."""
        contact = definition.contact

        # a monitor that has recovered starts its alert and error rate limits
        # over, so the next failure is reported straight away
        if result.status == ResultStatus.OK and self.throttle_gate is not None:
            self.throttle_gate.reset(
                definition.name,
                overrides={
                    contact_type: contact.renotify_after(contact_type)
                    for contact_type in CONTACT_TYPES
                },
            )

        if result.alerts and definition.monitor_type_alert:
            self._handle_alerts(definition, result)

        if result.reports and definition.monitor_type_report:
            self._handle_reports(definition, result)

        if result.errors and contact.error:
            self._render_and_send(definition, result, contact.error, "error")

        if contact.info:
            self._render_and_send(definition, result, contact.info, "info")

        if contact.notify:
            self._render_and_send(definition, result, contact.notify, "notify")

    def flush(self):
        """Send everything that was held back for combined delivery."""
        pending = self._pending
        self._pending = {}

        for contact, held in pending.items():
            if held.alerts:
                deliveries = self._send_batch(contact, "alert", held, held.alerts)
                self._record_batch("alert", held.alerts, deliveries)
            if held.reports:
                deliveries = self._send_batch(contact, "report", held, held.reports)
                self._record_batch("report", held.reports, deliveries)

    def send_test(
        self, definition: MonitorDefinition, contact_type: str
    ) -> list[Delivery]:
        """Send a test notification to one of a monitor's contact lists.

        The real template for the contact type and channel is rendered with
        placeholder values, so the message shows exactly what a live
        notification would look like.  Returns what happened per contact so the
        caller can tell a real delivery from a logged one, and a failure from
        either.
        """
        contacts = definition.contact.for_type(contact_type)
        if not contacts:
            return []

        result = self._sample_result(definition)
        alert = result.alerts[0] if contact_type == "alert" else None

        messages = []
        for contact, message in self._render(
            definition, result, contacts, contact_type, alert
        ):
            messages.append(
                (
                    contact,
                    RenderedMessage(
                        subject=f"test: {message.subject}",
                        body=message.body,
                        html=message.html,
                    ),
                )
            )

        return self._deliver(messages)

    # -- per monitor rendering -------------------------------------------

    def _handle_alerts(self, definition: MonitorDefinition, result: MonitorResult):
        contacts = definition.contact.alert
        if not contacts:
            logger.debug(
                f"Monitor '{definition.name}' raised {len(result.alerts)} alert(s) "
                f"but has no alert contacts configured"
            )
            return

        if not self._allowed(definition, result, "alert"):
            return

        status = result.status.value
        deliveries: list[Delivery] = []

        for alert in result.alerts:
            messages = self._render(definition, result, contacts, "alert", alert)

            if definition.contact.combine_alerts:
                for contact, message in messages:
                    held = self._pending.setdefault(contact, _Pending())
                    held.alerts.append(_Fragment(definition.name, message, status))
                continue

            deliveries.extend(self._deliver(messages))

        # the combined path records at flush time, when the batch is actually
        # delivered - there is nothing to record here
        if not definition.contact.combine_alerts:
            self._record(definition.name, "alert", status, deliveries)

    def _handle_reports(self, definition: MonitorDefinition, result: MonitorResult):
        contacts = definition.contact.report
        if not contacts:
            logger.debug(
                f"Monitor '{definition.name}' produced {len(result.reports)} report(s) "
                f"but has no report contacts configured"
            )
            return

        if not self._allowed(definition, result, "report"):
            return

        status = result.status.value
        messages = self._render(definition, result, contacts, "report")

        if definition.contact.combine_reports:
            for contact, message in messages:
                held = self._pending.setdefault(contact, _Pending())
                held.reports.append(_Fragment(definition.name, message, status))
            return

        self._record(definition.name, "report", status, self._deliver(messages))

    def _render_and_send(
        self,
        definition: MonitorDefinition,
        result: MonitorResult,
        contacts: list[Contact],
        contact_type: str,
    ) -> list[Delivery]:
        if not self._allowed(definition, result, contact_type):
            return []

        deliveries = self._deliver(
            self._render(definition, result, contacts, contact_type)
        )
        self._record(definition.name, contact_type, result.status.value, deliveries)

        return deliveries

    def _render(
        self,
        definition: MonitorDefinition,
        result: MonitorResult,
        contacts: list[Contact],
        contact_type: str,
        alert: Alert | None = None,
    ) -> list[tuple[Contact, RenderedMessage]]:
        """Render one message per contact.

        A template that fails to render loses that one message - the monitor
        run, and every other contact, carries on.
        """
        templates = definition.message_templates or self.templates
        messages: list[tuple[Contact, RenderedMessage]] = []

        for contact in contacts:
            context = self._context(definition, result, contact, contact_type, alert)
            try:
                message = templates.render(contact_type, contact.channel, context)
            except MessageTemplateError as e:
                logger.error(
                    f"Monitor '{definition.name}': skipping the '{contact_type}' "
                    f"notification to {contact} - {e}"
                )
                continue

            # a template that renders to nothing is how a monitor opts out of
            # a notification it has no content for
            if not message.body and not message.html:
                logger.debug(
                    f"Monitor '{definition.name}': the '{contact_type}' template "
                    f"for '{contact.channel}' rendered empty - nothing to send "
                    f"to {contact}"
                )
                continue

            messages.append((contact, message))

        return messages

    @staticmethod
    def _context(
        definition: MonitorDefinition,
        result: MonitorResult,
        contact: Contact,
        contact_type: str,
        alert: Alert | None = None,
    ) -> dict:
        context: dict[str, Any] = {
            "name": definition.name,
            "description": definition.description,
            "link": definition.link,
            "tags": definition.tags,
            "reports": list(result.reports),
            "test_results": list(result.test_results),
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "duration_seconds": result.duration_seconds,
            "status": result.status.value,
            "contact": contact,
            "contact_type": contact_type,
            "contact_channel": contact.channel,
        }

        # left undefined for the contact types that have no single alert, so
        # a shared template renders "(Undefined)" rather than "None"
        if alert is not None:
            context["alert"] = alert

        return context

    # -- rate limiting -----------------------------------------------------

    def _allowed(
        self,
        definition: MonitorDefinition,
        result: MonitorResult,
        contact_type: str,
    ) -> bool:
        """Whether the renotify delay lets this notification through.

        Checked before anything is rendered or queued, so a suppressed monitor
        never reaches a combined batch and never skews its subject counts.
        """
        if self.throttle_gate is None:
            return True

        return self.throttle_gate.allow(
            definition.name,
            contact_type,
            status=result.status.value,
            override=definition.contact.renotify_after(contact_type),
        )

    def _record(
        self,
        monitor_name: str,
        contact_type: str,
        status: str | None,
        deliveries: list[Delivery],
    ):
        """Start the renotify window, but only on a real delivery.

        A message that merely reached a logging sender, failed, or was dropped
        by the email quota has not told anybody anything - letting it start the
        window would suppress the notifications that follow it.
        """
        if self.throttle_gate is None:
            return

        if any(
            delivery.live and delivery.sent and not delivery.throttled
            for delivery in deliveries
        ):
            self.throttle_gate.record(monitor_name, contact_type, status)

    def _record_batch(
        self,
        contact_type: str,
        fragments: list[_Fragment],
        deliveries: list[Delivery],
    ):
        """Record the window for every monitor that contributed to a batch."""
        seen: set[str] = set()
        for fragment in fragments:
            if fragment.monitor_name in seen:
                continue
            seen.add(fragment.monitor_name)
            self._record(
                fragment.monitor_name, contact_type, fragment.status, deliveries
            )

    # -- combined delivery -----------------------------------------------

    def _send_batch(
        self,
        contact: Contact,
        contact_type: str,
        held: _Pending,
        fragments: list[_Fragment],
    ) -> list[Delivery]:
        """Merge the held fragments for one contact into a single message."""
        body = "\n\n".join(f.message.body for f in fragments if f.message.body)
        html_parts = [f.message.html for f in fragments if f.message.html]

        message = RenderedMessage(
            subject=self._batch_subject(contact, contact_type, held, fragments),
            body=self.templates.cap_body(contact.channel, body),
            html=(
                self.templates.cap_body(contact.channel, "".join(html_parts))
                if html_parts
                else None
            ),
        )

        return self._deliver([(contact, message)])

    def _batch_subject(
        self,
        contact: Contact,
        contact_type: str,
        held: _Pending,
        fragments: list[_Fragment],
    ) -> str:
        """The configured `batch_subject`, or the first fragment's subject."""
        context = {
            "monitor_count": len(held.monitor_names()),
            "monitor_names": held.monitor_names(),
            "alert_count": held.alert_count,
            "report_count": held.report_count,
            "contact": contact,
            "contact_type": contact_type,
            "contact_channel": contact.channel,
        }

        try:
            subject = self.templates.render_batch_subject(
                contact_type, contact.channel, context
            )
        except MessageTemplateError as e:
            logger.error(f"Falling back to the first monitor's subject - {e}")
            subject = ""

        return subject or fragments[0].message.subject

    # -- sending ----------------------------------------------------------

    def _deliver(
        self, messages: list[tuple[Contact, RenderedMessage]]
    ) -> list[Delivery]:
        """Deliver rendered messages, one channel at a time.

        Email contacts that were sent the identical message share a single
        email, so a contact list still produces one mail with several
        recipients.  A channel that fails is logged and skipped so the
        remaining contacts still hear about the run.
        """
        email_groups: dict[tuple[str, str, str | None], list[Contact]] = {}
        chat: list[tuple[Contact, RenderedMessage]] = []
        files: list[tuple[Contact, RenderedMessage]] = []
        texts: list[tuple[Contact, RenderedMessage]] = []

        for contact, message in messages:
            subject = f"{self.subject_prefix} {message.subject}"
            if contact.channel == EMAIL_CHANNEL:
                key = (subject, message.body, message.html)
                email_groups.setdefault(key, []).append(contact)
            elif contact.channel in FILE_CHANNELS:
                files.append(
                    (contact, RenderedMessage(subject, message.body, message.html))
                )
            elif contact.channel == TEXT_CHANNEL:
                texts.append((contact, RenderedMessage(subject, message.body)))
            else:
                chat.append((contact, RenderedMessage(subject, message.body)))

        deliveries: list[Delivery] = []
        for (subject, body, html), group in email_groups.items():
            deliveries.extend(self._send_email(group, subject, body, html))

        for contact, message in chat:
            deliveries.extend(
                self._send_chat(contact.channel, contact, message.subject, message.body)
            )

        for contact, message in files:
            deliveries.extend(
                self._send_file(
                    contact.channel,
                    contact,
                    message.subject,
                    message.body,
                    message.html,
                )
            )

        for contact, message in texts:
            deliveries.extend(self._send_text(contact, message.subject, message.body))

        return deliveries

    def _send_email(
        self, contacts: list[Contact], subject: str, body: str, html: str | None
    ) -> list[Delivery]:
        recipients = [contact.address for contact in contacts]
        live = self.email_sender.is_live

        try:
            outcome = self.email_sender.send(
                to=recipients, subject=subject, body=body, html=html
            )
        except Exception as e:
            logger.exception(f"Failed to send email '{subject}' to {recipients}: {e}")
            return [Delivery(contact, live, error=str(e)) for contact in contacts]

        # a sender with nothing to report sent to everyone
        throttled = set(outcome.throttled) if outcome else set()

        return [
            Delivery(contact, live, throttled=contact.address in throttled)
            for contact in contacts
        ]

    def _send_chat(
        self, channel: str, contact: Contact, subject: str, body: str
    ) -> list[Delivery]:
        sender = self.chat_senders.get(channel)
        if sender is None:
            message = f"No '{channel}' sender is configured"
            logger.error(f"{message} - cannot notify {contact.target}")
            return [Delivery(contact, False, error=message)]

        try:
            sender.send(target=contact.target, subject=subject, body=body)
        except Exception as e:
            logger.exception(
                f"Failed to send {channel} message '{subject}' to {contact.target}: {e}"
            )
            return [Delivery(contact, sender.is_live, error=str(e))]

        return [Delivery(contact, sender.is_live)]

    def _send_file(
        self,
        channel: str,
        contact: Contact,
        subject: str,
        body: str,
        html: str | None,
    ) -> list[Delivery]:
        sender = self.file_senders.get(channel)
        if sender is None:
            message = f"No '{channel}' sender is configured"
            logger.error(f"{message} - cannot notify {contact.target}")
            return [Delivery(contact, False, error=message)]

        try:
            sender.send(target=contact.target, subject=subject, body=body, html=html)
        except Exception as e:
            logger.exception(
                f"Failed to write {channel} message '{subject}' to {contact.target}: {e}"
            )
            return [Delivery(contact, sender.is_live, error=str(e))]

        return [Delivery(contact, sender.is_live)]

    def _send_text(self, contact: Contact, subject: str, body: str) -> list[Delivery]:
        sender = self.text_sender
        if sender is None:
            message = "No text sender is configured"
            logger.error(f"{message} - cannot notify {contact.target}")
            return [Delivery(contact, False, error=message)]

        try:
            sender.send(target=contact.target, subject=subject, body=body)
        except Exception as e:
            logger.exception(
                f"Failed to send text message '{subject}' to {contact.target}: {e}"
            )
            return [Delivery(contact, sender.is_live, error=str(e))]

        return [Delivery(contact, sender.is_live)]

    # -- test notifications -----------------------------------------------

    @staticmethod
    def _sample_result(definition: MonitorDefinition) -> MonitorResult:
        """Placeholder values so `test-notify` exercises the real template."""
        alert = Alert(
            message="This is a sample alert message",
            name="sample alert",
            value=42,
            threshold=10,
            details=f"Sample alert from '{definition.source_file}'",
        )
        report = Report(
            title="Sample report",
            columns=["Name", "Value"],
            rows=[["sample row 1", 1], ["sample row 2", 2]],
        )
        started_at = datetime.now()

        return MonitorResult(
            monitor_name=definition.name,
            status=ResultStatus.ALERT,
            started_at=started_at,
            finished_at=started_at,
            message="This is a sample monitor result",
            test_results=[
                TestResult(
                    key=f"{definition.name}.sample",
                    test_type="sample",
                    status=ResultStatus.ALERT,
                    message="This is a sample test message",
                    value=42,
                    alerts=[alert],
                    reports=[report],
                    error="This is a sample error message",
                )
            ],
        )
