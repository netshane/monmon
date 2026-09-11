"""Text message delivery used by the notifier.

Two providers, chosen globally by `settings.toml` (`[text] provider`):

- `imsg` shells out to the `imsg` cli tool to send an iMessage.  macOS only.
- `twilio` sends an sms through the Twilio api.

Both truncate an over-long body to `MAX_TEXT_LENGTH` characters and log a
warning when they do, so a caller never needs to cap the body itself.
"""

from __future__ import annotations

import platform
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from loguru import logger
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

MAX_TEXT_LENGTH = 160


@dataclass
class SentText:
    target: str
    subject: str
    body: str
    provider: str


class TextSender(ABC):
    """Sends a plain text message to a single phone number."""

    provider: str = "text"

    # False when the sender only records messages, so callers such as
    # `test-notify` can say "logged" rather than claiming a delivery
    is_live: bool = True

    @abstractmethod
    def send(self, target: str, subject: str, body: str):
        """Deliver a message to `target`."""

    def _cap(self, target: str, body: str) -> str:
        if len(body) <= MAX_TEXT_LENGTH:
            return body

        logger.warning(
            f"Text message to {target} is {len(body)} characters - truncating to "
            f"{MAX_TEXT_LENGTH}"
        )
        return body[:MAX_TEXT_LENGTH]


class LoggingTextSender(TextSender):
    """Logs text messages instead of sending them.

    This is what `--simulate` uses, and the default when no provider is
    configured, so a run never texts anyone by accident.
    """

    is_live = False

    def __init__(self, provider: str = "text"):
        self.provider = provider
        self.sent: list[SentText] = []

    def send(self, target: str, subject: str, body: str):
        capped = self._cap(target, body)
        self.sent.append(
            SentText(
                target=target, subject=subject, body=capped, provider=self.provider
            )
        )
        logger.info(
            f"SIMULATED TEXT ({self.provider}) to {target}: {subject}\n{capped}"
        )


class ImsgSender(TextSender):
    """Sends an iMessage through the `imsg` cli tool.

    `imsg` is macOS only - both "not on a mac" and "imsg not installed" are
    per-send failures so a monitor still loads and runs, it just does not
    deliver this one message.
    """

    provider = "imsg"

    def send(self, target: str, subject: str, body: str):
        if platform.system() != "Darwin":
            raise RuntimeError("imsg is only available on macOS")

        capped = self._cap(target, body)

        try:
            completed = subprocess.run(
                [
                    "imsg",
                    "send",
                    "--to",
                    target,
                    "--text",
                    capped,
                    "--service",
                    "imessage",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError("the 'imsg' cli tool is not installed") from e

        if completed.returncode != 0:
            raise RuntimeError(
                f"imsg failed to send to {target}: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )

        logger.info(f"Sent imessage to {target}: {subject}")


@dataclass
class TwilioSettings:
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)


class TwilioSender(TextSender):
    """Sends an sms through the Twilio api.

    Takes the `twilio.rest.Client` as a constructor argument, like every other
    sender takes its transport (`HttpClient`, `smtplib`) - the dependencies.py
    factory builds it, this class never builds its own.
    """

    provider = "twilio"

    def __init__(self, settings: TwilioSettings, client: TwilioClient):
        self.settings = settings
        self._client = client

    def send(self, target: str, subject: str, body: str):
        capped = self._cap(target, body)

        try:
            self._client.messages.create(
                to=target, from_=self.settings.from_number, body=capped
            )
        except TwilioRestException as e:
            raise RuntimeError(f"twilio failed to send to {target}: {e}") from e

        logger.info(f"Sent sms to {target}: {subject}")
