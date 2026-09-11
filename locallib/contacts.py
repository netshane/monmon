"""Contact lists for a monitor's `[contact]` section.

A contact is written as `<channel>:<target>`, e.g. `email:ops@example.com`,
`email:Ops Team:ops@example.com`, `slack:#alerts`, or `teams:@shane`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from email.utils import formataddr

EMAIL_CHANNEL = "email"
SLACK_CHANNEL = "slack"
TEAMS_CHANNEL = "teams"
FILE_CHANNEL = "file"
FILE_APPEND_CHANNEL = "file_append"
TEXT_CHANNEL = "text"

CHAT_CHANNELS = (SLACK_CHANNEL, TEAMS_CHANNEL)
FILE_CHANNELS = (FILE_CHANNEL, FILE_APPEND_CHANNEL)
CHANNELS = (EMAIL_CHANNEL, *CHAT_CHANNELS, *FILE_CHANNELS, TEXT_CHANNEL)

# permissive E.164-style check: an optional leading +, then digits with
# optional spaces/dashes/parentheses, 7-15 digits once those are stripped
_PHONE_PATTERN = re.compile(r"^\+?[\d\s\-()]+$")

CONTACT_TYPES = ("alert", "report", "info", "notify", "error")

# deliberately permissive - enough to catch typos and mis-split contacts
# without rejecting the addresses real mail servers accept
_EMAIL_PATTERN = re.compile(r"^[^@\s,;:<>]+@[^@\s,;:<>]+\.[A-Za-z]{2,}$")


class InvalidContactError(ValueError):
    """Raised when a contact string cannot be parsed or is not valid."""


@dataclass(frozen=True)
class Contact:
    """One notification destination."""

    channel: str
    target: str
    name: str | None = None

    @property
    def address(self) -> str:
        """The target as the channel wants it addressed.

        `formataddr` quotes and encodes the display name, so a name holding a
        comma or non-ascii characters cannot break the `To` header apart.
        """
        if self.channel == EMAIL_CHANNEL and self.name:
            return formataddr((self.name, self.target))

        return self.target

    def __str__(self) -> str:
        return f"{self.channel}:{self.address}"


def parse_contact(value, field: str | None = None) -> Contact:
    """Parse a single `<channel>:<target>` contact string."""
    where = f" in '{field}'" if field else ""

    if not isinstance(value, str):
        raise InvalidContactError(f"Invalid contact{where}: {value!r} is not a string")

    text = value.strip()
    if not text:
        raise InvalidContactError(f"Invalid contact{where}: the contact is empty")

    channel, separator, remainder = text.partition(":")
    channel = channel.strip().lower()

    if not separator:
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' has no channel prefix - "
            f"expected one of {', '.join(f'{c}:' for c in CHANNELS)}"
        )

    if channel not in CHANNELS:
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' uses unknown channel '{channel}' - "
            f"expected one of {', '.join(CHANNELS)}"
        )

    if channel == EMAIL_CHANNEL:
        return _parse_email(text, remainder, where)

    if channel in FILE_CHANNELS:
        return _parse_file(channel, text, remainder, where)

    if channel == TEXT_CHANNEL:
        return _parse_text(text, remainder, where)

    return _parse_chat(channel, text, remainder, where)


def parse_contact_list(value, field: str | None = None) -> list[Contact]:
    """Parse a toml contact list, keeping order and dropping duplicates."""
    if value is None:
        return []

    if isinstance(value, str):
        items = [part for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise InvalidContactError(
            f"Invalid contact list '{field or value}': expected a list of strings"
        )

    contacts: list[Contact] = []
    for item in items:
        contact = parse_contact(item, field=field)
        if contact not in contacts:
            contacts.append(contact)

    return contacts


def _parse_email(text: str, remainder: str, where: str) -> Contact:
    """`email:<address>` or `email:<name>:<address>`."""
    name, separator, address = remainder.rpartition(":")
    if not separator:
        name, address = "", remainder

    name = name.strip()
    address = address.strip()

    if not _EMAIL_PATTERN.match(address):
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' is not a valid email address - "
            f"expected 'email:<address>' or 'email:<name>:<address>'"
        )

    return Contact(channel=EMAIL_CHANNEL, target=address, name=name or None)


def _parse_file(channel: str, text: str, remainder: str, where: str) -> Contact:
    """`file:<path>` or `file_append:<path>`, relative to the current directory.

    The path is proven writable at parse time (rather than merely
    syntactically checked) by opening it for writing, so a monitor with a
    bad target fails to load instead of failing on its first notification.
    This always opens in append mode, even for `file` - monitor definitions
    are loaded far more often than they notify (e.g. every `list`, `show`,
    or `validate`), so validation must never truncate a target that already
    holds a previous report. `file` still overwrites at actual delivery
    time; only this load-time check is non-destructive.
    """
    target = remainder.strip()

    if not target:
        raise InvalidContactError(f"Invalid contact{where}: '{text}' has no file path")

    directory = os.path.dirname(target)

    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "a"):
            pass
    except OSError as e:
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' names a file that cannot be "
            f"opened for writing - {e}"
        ) from e

    return Contact(channel=channel, target=target)


def _parse_text(text: str, remainder: str, where: str) -> Contact:
    """`text:<phone_number>`, e.g. `text:+1 415-555-1212`."""
    target = remainder.strip()

    if not target:
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' has no phone number"
        )

    digits = re.sub(r"\D", "", target)

    if not _PHONE_PATTERN.match(target) or not (7 <= len(digits) <= 15):
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' is not a valid phone number - "
            f"expected 'text:<phone_number>' in a permissive E.164-style format "
            f"(digits, optionally with a leading +, spaces, dashes, or parentheses)"
        )

    return Contact(channel=TEXT_CHANNEL, target=target)


def _parse_chat(channel: str, text: str, remainder: str, where: str) -> Contact:
    """`slack:#channel`, `slack:@person`, and the teams equivalents."""
    target = remainder.strip()

    if not target.startswith(("#", "@")):
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' must name a {channel} channel with "
            f"'#' or a person with '@' (e.g. '{channel}:#alerts' or '{channel}:@shane')"
        )

    if len(target) < 2:
        raise InvalidContactError(
            f"Invalid contact{where}: '{text}' has no {channel} channel or person name"
        )

    return Contact(channel=channel, target=target)
