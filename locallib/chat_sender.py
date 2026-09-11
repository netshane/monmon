"""Slack and Teams delivery used by the notifier.

Each sender delivers one plain text message to one target (`#channel` or
`@person`).  `LoggingChatSender` is the offline equivalent of
`LoggingEmailSender` - it records what would have been sent so the channels
can be exercised without any network calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from loguru import logger

from .http_client import HttpClient

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


@dataclass
class SentMessage:
    channel: str
    target: str
    subject: str
    body: str


class ChatSender(ABC):
    """Sends a plain text message to a single chat target."""

    channel: str = "chat"

    # False when the sender only records messages, so callers such as
    # `test-notify` can say "logged" rather than claiming a delivery
    is_live: bool = True

    @abstractmethod
    def send(self, target: str, subject: str, body: str):
        """Deliver a message to `target`."""


class LoggingChatSender(ChatSender):
    """Logs chat messages instead of sending them.

    This is the default when no slack/teams credentials are configured, and it
    is what `--simulate` uses so a run never messages anyone by accident.
    """

    is_live = False

    def __init__(self, channel: str, prefix: str | None = None):
        self.channel = channel
        self.prefix = prefix or channel.upper()
        self.sent: list[SentMessage] = []

    def send(self, target: str, subject: str, body: str):
        self.sent.append(
            SentMessage(channel=self.channel, target=target, subject=subject, body=body)
        )
        logger.info(f"{self.prefix} to {target}: {subject}\n{body}")


@dataclass
class SlackSettings:
    """Either a bot `token` (preferred) or an incoming `webhook_url`."""

    token: str | None = None
    webhook_url: str | None = None
    webhooks: dict = field(default_factory=dict)

    @property
    def is_configured(self) -> bool:
        return bool(self.token or self.webhook_url or self.webhooks)


class SlackSender(ChatSender):
    """Posts to slack through the web api, or through incoming webhooks."""

    channel = "slack"

    def __init__(self, settings: SlackSettings, http_client: HttpClient):
        self.settings = settings
        self.http_client = http_client

    def send(self, target: str, subject: str, body: str):
        text = f"*{subject}*\n{body}" if subject else body

        if self.settings.token:
            response = self.http_client.post_json(
                SLACK_POST_MESSAGE_URL,
                {"channel": target, "text": text},
                headers={"Authorization": f"Bearer {self.settings.token}"},
            )
            _raise_for_status(response, "slack", target)

            payload = _json_or_none(response)
            if payload is not None and not payload.get("ok", False):
                raise RuntimeError(
                    f"Slack rejected the message to {target}: "
                    f"{payload.get('error', 'unknown error')}"
                )
        else:
            url = self.settings.webhooks.get(target) or self.settings.webhook_url
            if not url:
                raise ValueError(f"No slack webhook is configured for '{target}'")

            response = self.http_client.post_json(
                url, {"channel": target, "text": text}
            )
            _raise_for_status(response, "slack", target)

        logger.info(f"Sent slack message to {target}: {subject}")


@dataclass
class TeamsSettings:
    """Teams incoming webhooks, either one default or one per target."""

    webhook_url: str | None = None
    webhooks: dict = field(default_factory=dict)

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url or self.webhooks)


class TeamsSender(ChatSender):
    """Posts to a teams channel through an incoming webhook."""

    channel = "teams"

    def __init__(self, settings: TeamsSettings, http_client: HttpClient):
        self.settings = settings
        self.http_client = http_client

    def send(self, target: str, subject: str, body: str):
        url = self.settings.webhooks.get(target) or self.settings.webhook_url
        if not url:
            raise ValueError(f"No teams webhook is configured for '{target}'")

        response = self.http_client.post_json(
            url,
            {
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": subject or "monitor notification",
                "title": subject,
                "text": body,
            },
        )
        _raise_for_status(response, "teams", target)

        logger.info(f"Sent teams message to {target}: {subject}")


def _raise_for_status(response, channel: str, target: str):
    if response.status_code >= 400:
        raise RuntimeError(
            f"{channel} returned {response.status_code} for '{target}': {response.text}"
        )


def _json_or_none(response):
    try:
        return response.json()
    except Exception:
        return None
