Update the format of the [contact] configuration.
There should be 5 types of contacts:
- alert - all contacts in alert should be notified whenever alerts are generated for the current monitor
- report - all contacts in report should be notified whenever a report monitor completes successfully. The messages of all tests should be included in the notification
- info - all contacts in this list should be notified with all the tests run and the results of those tests whenever the monitor runs
- notify - all contacts in this list should be notified when the monitor has run.  This should be a short notification containing monitor name, result, and run time
- error - all contacts in this list should be notified with any errors or exceptions that occur while running the monitor

The following format should be used for all contact lists:
```
alert = [
   "email:shane@netshane.com",
   "slack:#channel",
   "slack:@person",
   "teams:#channel",
   "teams:@person",
]
```



This new format should replace the existing `alert_email` format.  That format should be deprecated and removed from the code base.  The new format should be used for all new monitors and existing monitors should be updated to use the new format.


Also implement slack and teams communinication channels and add stubs / examples for the configuration to `settings.toml`

For the slack and teams channels: # indicates a channel and @ indicates a person.  All contacts must be configured this way.  If the contact does not start with # or @ it should be treated as an invalid configuration and the monitor should fail to load with an error message indicating the invalid contact.

Email contacts should be configured as `email:<email address>` or `email:<name>:<email address>`.  Verify that the email address is formatted correctly and if not the monitor should fail to load with an error message indicating the invalid contact.

The configuration for batching notifiations should be updated from `combine_report_emails` to `combine_reports` and `combine_alert_emails` to `combine_alerts`.  
Notifications should be batched by contact type and channel

For phase 1 of this implementation all notifications should be plain text.
In the next phase we will implement templates to allow for html, markdown, and other formats for notifications.

If a notiication fails to send, the monitor should log the error and continue to send notifications to other contacts.  The monitor should not fail to run if a notification fails to send.

The Slack and Teams notification channels should be testable without having to make live network calls, consistent with LoggingEmailSender.

Add a cli function to send a test notification to a contact list.  This should be a simple command that specifies the monitor and contact type.

---

## Implementation summary

Claude session: https://claude.ai/code/session_0116YWK3Gc5FuEHAo8XEZWec

### New modules

- `locallib/contacts.py` - `Contact` (channel/target/name) plus `parse_contact` /
  `parse_contact_list`.  Validates `email:<addr>`, `email:<name>:<addr>`
  (regex-checked), and `slack:` / `teams:` targets that must start with `#` or
  `@`.  Anything else raises `InvalidContactError`, which surfaces as a monitor
  load failure naming the offending contact.
- `locallib/chat_sender.py` - `ChatSender` ABC with `SlackSender` (bot token ->
  `chat.postMessage`, else incoming webhook), `TeamsSender` (per-target or
  default webhook), and `LoggingChatSender`, the offline counterpart of
  `LoggingEmailSender`.  `HttpClient.post_json` was added for the posts.

### Changed

- `ContactConfig` is now five contact lists (`alert` / `report` / `info` /
  `notify` / `error`) plus `combine_alerts` / `combine_reports`.  The old
  `*_email` and `combine_*_emails` keys are removed - a monitor still using them
  fails to load with a message naming the replacement.
- `Notifier` dispatches per channel, batching held messages keyed by contact (so
  per contact type *and* channel).  A failing channel or chat target is logged
  and the rest still go out.  Report notifications include the messages of all
  tests alongside the tables.
- `MonitorLoader` records load errors - invalid contacts, unparsable files, and
  duplicate monitor names; `validate` reports them, so a dropped monitor is a
  warning and a non-zero exit rather than a silent skip.
- CLI: `test-notify <monitor> <type>` sends a test notification, and
  `contacts <monitor>` lists resolved contacts.  `test-notify` reports the real
  outcome per contact (`sent` / `logged` / `FAILED: <reason>`) and exits 1 when
  any delivery failed, so it cannot claim success for a message that only
  reached a logging sender or never left the process.
- `settings.toml` gained `[default.slack]` / `[default.teams]` stubs (token,
  `webhook_url`, per-target `webhooks` tables), `secrets.sample.toml` gained
  commented-out `slack` / `email` password entries, and `monitors/sample.toml`,
  `monitors/examples/localhost.toml`, `README.md`, `docs/monitors.md` were
  updated to the new format.

### Review follow ups

A code review of the change set produced seven findings, all fixed:

1. `Contact.address` built the `To` display name by hand, so a name holding a
   comma split the header into a bogus recipient.  It now uses
   `email.utils.formataddr`, which quotes and encodes the name.
2. `test-notify` always reported success because delivery exceptions are
   swallowed (correct during a monitor run).  `Notifier._send` now returns a
   `Delivery` per contact, senders carry an `is_live` flag, and the CLI reports
   and exits on the real outcome.
3. The chat senders were built with `get_http_client()`, so `http.verify_ssl =
   false` (meant for probing a monitored host with a self signed certificate)
   also disabled verification on the request carrying the Slack bot token.  They
   now use `get_notification_http_client()`, pinned to `verify_ssl=True`.
4. Monitor names, report titles, and test messages were interpolated into the
   report html unescaped while table cells were escaped.  `helpers._escape` is
   now the public `escape_html` and is applied to all of them.
5. `_test_messages` left a dangling `-` separator for a test with no message.
   The message is now appended only when present.
6. `MonitorLoader` recorded parse failures but not the duplicate-name branch, so
   two files sharing a `name` still dropped one silently with `validate` exiting
   0.  That message is recorded too.
7. `secrets.sample.toml` shipped `slack = "##FIXME##"`, a truthy placeholder that
   made a copied secrets file attempt live posts that fail with `invalid_auth`
   instead of falling back to `LoggingChatSender`.  It is commented out.

### Judgment calls

- Uncombined report notifications send one message per monitor run containing all
  its reports (previously one per report), since the spec asks for all test
  messages in the report notification.
- Teams `@person` targets only work when mapped to a webhook in
  `[default.teams.webhooks]`, as Teams incoming webhooks are channel-scoped and
  have no direct-message equivalent.

### Verification

275 unit tests pass (`uv run pytest`), `uvx ruff check` is clean, and the CLI was
exercised end to end against real monitor files: `validate`, `contacts`,
`test-notify` (email + slack + teams in simulate mode), an invalid
`slack:monitoring` contact correctly failing the monitor load, a duplicate
monitor name reported by `validate`, a quoted `"Ops, Night" <ops@example.com>`
recipient, and `test-notify` against an unreachable smtp host reporting
`FAILED: [Errno 61] Connection refused` and exiting non-zero.
