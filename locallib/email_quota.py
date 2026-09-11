"""Cross run send quota for the email channel.

`run-scheduled` is a batch process, so a fresh sender knows nothing about the
mail earlier invocations sent.  The gate here answers that from the
`email_send_log` table instead: both limits are rolling windows over the log,
counted per recipient address because that is how the smtp relay counts them.

Counts are keyed on the bare mailbox rather than on the `Display Name <box>`
form a named contact renders as, so one mailbox is one recipient however many
monitors address it and whatever display name they give it.  The addresses
handed back to the sender keep their original form - only the bookkeeping is
normalised.

Nothing in this module reads settings - `dependencies.get_email_quota()` builds
the `EmailQuota` and injects it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parseaddr

from loguru import logger

from .monitor_repository import MonitorRepository


@dataclass
class EmailQuota:
    """The configured limits.  0 (or absent) means the limit is unset."""

    # maximum recipient-sends in any rolling 24 hours, across all monitors
    max_per_day: int = 0
    # maximum sends to any one address within `cooldown_window_minutes`
    max_per_recipient: int = 0
    cooldown_window_minutes: int = 60

    @property
    def enabled(self) -> bool:
        return self.max_per_day > 0 or self.max_per_recipient > 0


class EmailQuotaGate:
    """Decides which recipients may be mailed, and logs the ones that were.

    `clock` is injected so tests can move the window without sleeping.
    """

    def __init__(
        self,
        repository: MonitorRepository,
        quota: EmailQuota,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.repository = repository
        self.quota = quota
        self.clock = clock
        # a quota exhaustion can drop thousands of messages; only the first
        # drop of a run is worth a warning, the rest go to debug
        self._drop_reported = False
        self._exhaustion_reported = False

    def allow(
        self, recipients: list[str], subject: str | None = None
    ) -> tuple[list[str], dict[str, str]]:
        """Split `recipients` into the ones that may be mailed and the rest.

        Returns the allowed addresses and a `{recipient: reason}` map for the
        ones a limit blocked.  Order is preserved, and a duplicate address is
        only counted once.
        """
        unique: list[str] = []
        seen: set[str] = set()
        for recipient in recipients:
            mailbox = _mailbox(recipient)
            if mailbox in seen:
                continue
            seen.add(mailbox)
            unique.append(recipient)

        if not self.quota.enabled or not unique:
            return unique, {}

        now = self.clock()
        blocked: dict[str, str] = {}

        survivors = self._apply_cooldown(unique, now, blocked)
        allowed = self._apply_daily_quota(survivors, now, blocked)

        for recipient, reason in blocked.items():
            self._log_drop(recipient, reason, subject)

        return allowed, blocked

    def record(
        self,
        recipients: list[str],
        subject: str | None = None,
        monitor_name: str | None = None,
    ):
        """Log a delivered email, one row per recipient."""
        if not recipients:
            return

        self.repository.record_email_sends(
            recipients=[_mailbox(recipient) for recipient in recipients],
            monitor_name=monitor_name,
            subject=subject,
            sent_at=self.clock(),
        )

    # -- limits ------------------------------------------------------------

    def _apply_cooldown(
        self, recipients: list[str], now: datetime, blocked: dict[str, str]
    ) -> list[str]:
        """Drop the addresses that are over the per recipient cooldown."""
        limit = self.quota.max_per_recipient
        if limit <= 0:
            return recipients

        cutoff = now - timedelta(minutes=self.quota.cooldown_window_minutes)
        counts = self.repository.count_email_sends_since_by_recipient(
            cutoff, [_mailbox(recipient) for recipient in recipients]
        )
        reason = (
            f"per recipient cooldown ({limit} in "
            f"{self.quota.cooldown_window_minutes} minutes)"
        )

        allowed = []
        for recipient in recipients:
            if counts.get(_mailbox(recipient), 0) >= limit:
                blocked[recipient] = reason
            else:
                allowed.append(recipient)

        return allowed

    def _apply_daily_quota(
        self, recipients: list[str], now: datetime, blocked: dict[str, str]
    ) -> list[str]:
        """Trim `recipients` to what is left of the rolling 24 hour quota."""
        if self.quota.max_per_day <= 0 or not recipients:
            return recipients

        cutoff = now - timedelta(hours=24)
        sent = self.repository.count_email_sends_since(cutoff)
        remaining = max(self.quota.max_per_day - sent, 0)

        if remaining >= len(recipients):
            return recipients

        if remaining == 0 and not self._exhaustion_reported:
            self._exhaustion_reported = True
            logger.error(
                f"Email daily quota exhausted - {sent} of {self.quota.max_per_day} "
                f"recipient-sends used in the last 24 hours; further mail is "
                f"being dropped"
            )

        reason = f"daily quota ({self.quota.max_per_day} in 24 hours)"
        for recipient in recipients[remaining:]:
            blocked[recipient] = reason

        return recipients[:remaining]

    def _log_drop(self, recipient: str, reason: str, subject: str | None):
        message = f"Email to {recipient} dropped by the {reason}: {subject or ''}"
        if self._drop_reported:
            logger.debug(message)
            return

        self._drop_reported = True
        logger.warning(message)


def _mailbox(address: str) -> str:
    """The bare mailbox of `Display Name <box@example.com>`.

    Falls back to the address as given when it holds no angle bracket form,
    so a plain address is its own key.
    """
    return parseaddr(address)[1] or address
