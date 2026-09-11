"""Email delivery used by the notifier."""

from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage

from loguru import logger

from .email_quota import EmailQuotaGate


@dataclass
class SentEmail:
    to: list[str]
    subject: str
    body: str
    html: str | None = None


@dataclass
class SendOutcome:
    """What a sender did with the recipient list it was given.

    Only the quota limited sender has anything to report; every other sender
    returns `None`, which the notifier reads as "everything was sent".
    """

    throttled: list[str] = field(default_factory=list)


class EmailSender(ABC):
    """Sends an email to one or more addresses."""

    # False when the sender only records messages, so callers such as
    # `test-notify` can say "logged" rather than claiming a delivery
    is_live: bool = True

    @abstractmethod
    def send(
        self, to: list[str], subject: str, body: str, html: str | None = None
    ) -> SendOutcome | None:
        """Deliver a message, returning what was dropped rather than sent."""


class LoggingEmailSender(EmailSender):
    """Logs emails instead of sending them.

    This is the default when no smtp host is configured, and it is what
    `--simulate` uses so a run never emails anyone by accident.
    """

    is_live = False

    def __init__(self, prefix: str = "EMAIL"):
        self.prefix = prefix
        self.sent: list[SentEmail] = []

    def send(self, to: list[str], subject: str, body: str, html: str | None = None):
        self.sent.append(SentEmail(to=list(to), subject=subject, body=body, html=html))
        logger.info(f"{self.prefix} to {', '.join(to)}: {subject}\n{body}")


@dataclass
class SmtpSettings:
    host: str
    port: int = 25
    from_address: str = "monitors@localhost"
    username: str | None = None
    password: str | None = None
    use_tls: bool = False
    timeout_seconds: int = 30
    extra_headers: dict = field(default_factory=dict)


class SmtpEmailSender(EmailSender):
    """Sends mail through an smtp server."""

    def __init__(self, settings: SmtpSettings):
        self.settings = settings

    def send(self, to: list[str], subject: str, body: str, html: str | None = None):
        message = EmailMessage()
        message["From"] = self.settings.from_address
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        for header, value in self.settings.extra_headers.items():
            message[header] = value

        message.set_content(body)
        if html:
            message.add_alternative(html, subtype="html")

        with smtplib.SMTP(
            self.settings.host,
            self.settings.port,
            timeout=self.settings.timeout_seconds,
        ) as smtp:
            if self.settings.use_tls:
                smtp.starttls()
            if self.settings.username:
                smtp.login(self.settings.username, self.settings.password or "")
            smtp.send_message(message)

        logger.info(f"Sent email to {', '.join(to)}: {subject}")


class QuotaLimitedEmailSender(EmailSender):
    """Applies the cross run send quota in front of another sender.

    A decorator rather than a branch inside `SmtpEmailSender`, so the smtp
    class stays a pure transport and the gate composes with any future sender.
    Only rows for mail that actually left are logged - a send that raises does
    not consume quota.
    """

    def __init__(self, inner: EmailSender, gate: EmailQuotaGate):
        self.inner = inner
        self.gate = gate

    @property
    def is_live(self) -> bool:
        return self.inner.is_live

    def send(
        self, to: list[str], subject: str, body: str, html: str | None = None
    ) -> SendOutcome:
        allowed, blocked = self.gate.allow(list(to), subject)
        outcome = SendOutcome(throttled=list(blocked))

        # no smtp connection is opened when every recipient is over a limit
        if not allowed:
            return outcome

        self.inner.send(to=allowed, subject=subject, body=body, html=html)

        # the mail has already left; a send log failure must not turn a
        # delivered message into a reported failure
        try:
            self.gate.record(allowed, subject)
        except Exception as e:
            logger.error(f"Failed to record the email send to {allowed}: {e}")

        return outcome
