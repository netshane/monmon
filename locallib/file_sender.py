"""File delivery used by the notifier.

`file` overwrites its target on every delivery; `file_append` appends to it,
separating deliveries with a blank line.  Both pick html or plain text per
delivery from the target's extension - `.html` writes `html`, anything else
(including no extension) writes `body`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger


@dataclass
class SentFile:
    target: str
    subject: str
    body: str
    html: str | None = None


class FileSender:
    """Writes a rendered message to a file target."""

    # False when the sender only records messages, so callers such as
    # `test-notify` can say "logged" rather than claiming a delivery
    is_live: bool = True

    def __init__(self, append: bool = False):
        self.append = append

    def send(self, target: str, subject: str, body: str, html: str | None = None):
        content = html if html and target.lower().endswith(".html") else body

        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)

        mode = "a" if self.append else "w"
        with open(target, mode, encoding="utf-8") as f:
            if self.append and f.tell() > 0:
                f.write("\n\n")
            f.write(content)

        verb = "Appended to" if self.append else "Wrote"
        logger.info(f"{verb} file {target}: {subject}")


class LoggingFileSender(FileSender):
    """Records what would have been written instead of writing it.

    This is what `--simulate` uses so a run never touches disk by accident.
    """

    is_live = False

    def __init__(self, append: bool = False, prefix: str | None = None):
        super().__init__(append=append)
        self.prefix = prefix or ("FILE_APPEND" if append else "FILE")
        self.sent: list[SentFile] = []

    def send(self, target: str, subject: str, body: str, html: str | None = None):
        self.sent.append(SentFile(target=target, subject=subject, body=body, html=html))
        logger.info(f"{self.prefix} to {target}: {subject}\n{body}")
