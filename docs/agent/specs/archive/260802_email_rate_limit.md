# Email rate limit - cross-run send quota

## Problem

`run-scheduled` is a batch process invoked from cron.  Every invocation builds
a fresh `Notifier` and a fresh `EmailSender`, so nothing in the application
knows how much mail it has already sent.  Two things go wrong as a result:

- The smtp relay is Amazon SES (`[default.email]` in `settings.toml`), which
  enforces a 24 hour sending quota counted **per recipient**.  A broad contact
  list plus a bad afternoon can exhaust it, and once exhausted every later
  notification - including the ones that matter - is rejected.
- A monitor that is failing continuously emails its alert contacts on every
  scheduled run.  Nothing throttles that today.

This feature adds a send quota that persists between runs, so limits apply
across the whole day rather than within a single process.

Intra-run pacing (a per second token bucket, for SES's rate limit rather than
its daily quota) is a separate, smaller change and is **out of scope here**.

## Scope

- Email channel only.  Slack, Teams, and the file channels are unaffected.
- Counting is **per recipient address**, not per `send()` call.
  `Notifier._deliver()` already groups contacts that were sent an identical
  message into one email with several recipients; SES counts each recipient
  separately and so must this feature.
- Limits are global to the deployment, configured in `settings.toml`.  There
  is no per monitor override - per the architecture rules, no module below
  `dependencies.py` reads settings.

## Behaviour

Two independent limits, either of which may be left unset (unset = no limit):

1. **Daily quota** - the maximum number of recipient-sends in a rolling 24
   hour window, across all monitors.  This is the SES quota guard.
2. **Per recipient cooldown** - the maximum number of sends to a single
   address within a rolling window.  This is the flood guard.

Both are evaluated as rolling windows against the send log described below,
not calendar days, so a cron schedule crossing midnight behaves predictably.

### When a limit is reached

The message is **dropped, not delayed**.  Blocking would push the monitors
still to run past their schedules, and a monitor's next run will produce a
fresh notification anyway.

A dropped message must be visible:

- `Delivery` gains a `throttled: bool` field.  `Delivery.status` returns
  `"throttled"` for it, alongside the existing `sent` / `logged` / `FAILED`.
  A throttled delivery is not an error - `Delivery.sent` stays True-adjacent
  semantics aside, `error` is left `None` and the run does not fail.
- A `logger.warning` names the recipient, the limit that was hit, and the
  subject that was dropped.
- The first time a limit is hit within a run, one summary line is logged; the
  remaining drops in that run log at debug level so a quota exhaustion does
  not produce thousands of warnings.

### Recipients are evaluated individually

A single `send()` for five recipients where two are over their cooldown sends
to the remaining three and reports two throttled deliveries.  If every
recipient is over the limit, no smtp connection is opened at all.

### Simulate and non-persisting runs must not consume quota

`--simulate` swaps in `LoggingEmailSender`, and `persist_results=False`.
Neither may write to the send log or count against a limit.  The quota gate is
only wired in when the sender is live.  This deserves an explicit test - it is
the easy bug in this feature.

## Configuration

```toml
[default.email]
# ... existing host / port / from_address / use_tls / subject_prefix ...

# maximum recipient-sends in any rolling 24 hours.  0 or absent = unlimited
max_per_day = 0

# maximum sends to any one address within `cooldown_window_minutes`.
# 0 or absent = unlimited
max_per_recipient = 0
cooldown_window_minutes = 60

# how long send log rows are kept.  Must exceed the longest window above
send_log_retention_days = 7
```

## Data model

A new table in `locallib/monitor_repository.py`, plain SQLAlchemy Core like
the rest of the schema so the `results_db` can still become postgres:

```
email_send_log
  id            Integer, pk, autoincrement
  recipient     String(320), not null, indexed
  monitor_name  String(255), nullable   -- null for batched / test sends
  subject       Text, nullable
  sent_at       DateTime, not null, indexed
```

A row is written per recipient per successful send.  A send that raises is not
logged and does not consume quota.

Rows are pruned by `send_log_retention_days`.  Extend the existing `purge`
command path rather than adding a new one - `purge_runs_before` already owns
history cleanup, and this table should be cleaned alongside it.

Repository methods:

- `record_email_sends(recipients, monitor_name, subject, sent_at)` - one
  insert per recipient, in a single transaction.
- `count_email_sends_since(cutoff)` - total rows at or after `cutoff`.
- `count_email_sends_since_by_recipient(cutoff, recipients)` - a
  `{recipient: count}` map, one query for the whole group rather than one per
  address.
- `purge_email_send_log_before(cutoff)` - returns rows deleted.

## Design

Keep the decision in the **sender layer**, not in `Notifier`.  The notifier
already carries enough branching, and a sender level gate covers `test-notify`
and any future caller for free.

New module `locallib/email_quota.py`:

- `EmailQuota` - a dataclass of the configured limits
  (`max_per_day`, `max_per_recipient`, `cooldown_window_minutes`), with an
  `enabled` property that is False when every limit is unset.
- `EmailQuotaGate` - takes a `MonitorRepository` and an `EmailQuota` in its
  constructor, plus an injected `clock` callable defaulting to
  `datetime.now` so the tests need no sleeping.
  - `allow(recipients) -> tuple[list[str], dict[str, str]]` returns the
    recipients that may be sent to, and a `{recipient: reason}` map for the
    ones that may not.
  - `record(recipients, subject)` writes the log rows.

New sender in `locallib/email_sender.py`:

- `QuotaLimitedEmailSender(EmailSender)` wraps an inner `EmailSender` plus an
  `EmailQuotaGate`.  `is_live` delegates to the inner sender.  `send()` filters
  the recipient list through the gate, calls the inner sender with what
  survives (or returns without sending when nothing does), then records.
  A decorator keeps `SmtpEmailSender` a pure transport and composes with the
  logging variant for free.

The gate needs to tell the notifier *which* recipients were throttled so
`Delivery` can report it.  `EmailSender.send()` returns `None` today.  Change
its contract to return an optional `SendOutcome` (`throttled: list[str]`);
existing senders return `None` and `Notifier._send_email()` treats `None` as
"everything sent", so no other sender needs to change.

Wiring in `locallib/dependencies.py`:

- `get_email_quota()` reads the three settings keys.
- `get_email_sender(simulate)` builds the `SmtpEmailSender` as it does now,
  then wraps it in `QuotaLimitedEmailSender` only when not simulating, the
  host is configured, and the quota is enabled.  The gate gets the repository
  from the existing `get_monitor_repository()`.

## Files to change

| File | Change |
| --- | --- |
| `locallib/monitor_repository.py` | `email_send_log` table, four methods, purge hook |
| `locallib/email_quota.py` | new - `EmailQuota`, `EmailQuotaGate` |
| `locallib/email_sender.py` | `SendOutcome`, `QuotaLimitedEmailSender` |
| `locallib/notifier.py` | `Delivery.throttled` / `status`, `_send_email` handling |
| `locallib/dependencies.py` | `get_email_quota()`, wrap in `get_email_sender()` |
| `monmon.py` | surface throttled counts in `test-notify` output; purge wording |
| `settings.toml` | the `[default.email]` keys above, with comments |
| `docs/architecture.md` | note the gate in the notification channel recipe |
| `README.md` | settings reference for the new keys |

## Tests

`tests/unit/test_email_quota.py` (new), plus cases in
`tests/unit/test_email_sender.py`, `tests/unit/test_notifier.py`, and
`tests/unit/test_monitor_repository.py`.  Per the existing convention, classes
are constructed directly with fakes - a fake repository and an injected clock,
no real database and no sleeping.  Cover at minimum:

- under the limit, everything sends and is logged;
- daily quota exhausted, nothing sends, no smtp connection attempted;
- partial - some recipients over cooldown, the rest still send;
- a send that raises does not consume quota;
- `--simulate` writes no log rows;
- rolling window boundary - a row exactly at the cutoff.

## Additional considerations

- error contact should not bypass the quota.
- if the daily quota is exhausted an error should be logged.


---

## Implementation summary

Implemented as specified.  Claude session
`session_01UpcPAZtjWCoNjHMW2xvD38`.

**`locallib/monitor_repository.py`** - new `email_send_log` table (id,
recipient, monitor_name, subject, sent_at; recipient and sent_at indexed) and
four methods: `record_email_sends()` (one insert per recipient in a single
transaction), `count_email_sends_since()`,
`count_email_sends_since_by_recipient()` (one grouped query for the whole
group), `purge_email_send_log_before()`.

**`locallib/email_quota.py`** (new) - `EmailQuota` holds the three limits with
an `enabled` property, `EmailQuotaGate` takes the repository, the quota, and an
injected `clock`.  `allow(recipients, subject=None)` de-duplicates the address
list, applies the per recipient cooldown first and then trims what survives to
the remaining daily quota, returning `(allowed, {recipient: reason})`.
`record()` writes the log rows.  The first drop of a run logs a warning naming
the recipient, the limit, and the subject; later drops go to debug, and a fully
exhausted daily quota logs one error.  `allow()` gained the optional `subject`
argument the spec's log line needs.

**`locallib/email_sender.py`** - `SendOutcome(throttled: list[str])`, the new
optional return of `EmailSender.send()`, and `QuotaLimitedEmailSender`, a
decorator that filters through the gate, opens no connection when nothing
survives, calls the inner sender, and only then records (so a raise consumes no
quota).  `is_live` delegates to the inner sender.

**`locallib/notifier.py`** - `Delivery.throttled`, `status` returning
`"throttled"`, and `_send_email()` reading the outcome; `None` still means
everything was sent.

**`locallib/dependencies.py`** - `get_email_quota()`,
`get_email_send_log_retention_days()`, and the wrap in `get_email_sender()`,
applied only when not simulating, the host is configured, and the quota is
enabled.

**`monmon.py`** - `test-notify` counts throttled contacts separately from
delivered and logged; `purge` prunes the send log by
`email.send_log_retention_days` (deployment wide, so it is skipped when
`--monitor` limits the purge to one monitor).  A single `purge_runs_before`
signature change was avoided in favour of the second repository call, keeping
the run count and the send log count separately reportable.

**Docs / settings** - the five `[default.email]` keys with comments, the
settings table in `README.md`, and the send log plus the gate in
`docs/architecture.md`.

**Tests** - `tests/unit/test_email_quota.py` and
`tests/unit/test_email_sender.py` (both new), plus cases in
`test_notifier.py`, `test_monitor_repository.py`, and `test_purge_commands.py`:
under the limit, daily exhaustion with no connection opened, partial drops, a
raising send, window boundaries at exactly 24h and exactly the cooldown, and
`--simulate` writing no rows.  `uv run pytest tests/unit` passes apart from the
pre-existing `test_opensearch_report_builds_one_row_per_hit` failure, which
fails on `main` too and is unrelated to this feature.

### Review follow-ups

A code review of the implementation raised six findings; four were fixed in the
same session:

1. `Contact.address` renders a named contact as `Display Name <box@x.com>`, so
   the quota was keyed on that string - the same mailbox counted twice when two
   monitors named it differently, and a rename reset its history.
   `EmailQuotaGate` now normalises with `email.utils.parseaddr` for counting,
   de-duplication, and the log rows; the addresses handed to the sender keep
   their original form so the `To` header is unchanged.
2. `gate.record()` ran inside the `try` that `Notifier._send_email` wraps around
   `send()`, so a send log write failure reported an already delivered message
   as `FAILED`.  The record call now catches and logs instead of propagating.
3. The `[default.email]` comment described `max_per_day` as per recipient; it is
   a deployment wide total, and now says so.
4. `purge` could delete rows still inside an active window, silently resetting a
   cooldown.  `get_email_send_log_retention_days()` now raises the configured
   retention to cover the longest configured window and warns when it does.

Left as they are: the check-then-insert race between two overlapping
`run-scheduled` processes can overshoot `max_per_day` by about one batch (the
spec's design, and locking is not worth it for a guard with headroom), and
`email_send_log.monitor_name` stays nullable and unset - the sender level gate
has no monitor in hand, and the column is kept for a future per monitor caller.
