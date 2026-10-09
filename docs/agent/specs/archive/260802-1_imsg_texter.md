allow the `text` contact type to be handled using the cli tool `imsg` if the user is on a mac
- the texting provider to use should be configurable in `settings.toml`
- verify the system is a mac before attempting to use `imsg`.  If the system is not a mac, the monitor should fail on a per-send basis and log an error message indicating that `imsg` is only available on macs.  The monitor should still load and run, but the text message will not be sent.
- `twilio` should be a configurable texting provider
- implement a `TwilioSender` class that uses the Twilio API to send text messages
-add a new contact type `text` that sends a text mesage using the configured provider.  It should be formatted as `text:<phone_number>` where `<phone_number>` is the phone number to send the text to.  The phone number should be validated to ensure it is a valid phone number format.
    - use a permissive E.164 format for phone number validation, allowing for optional `+` and country code, and allowing for spaces, dashes, and parentheses in the number.
- text messages should have a maximum length of 160 characters.  If the message is longer than 160 characters, it should be truncated and a warning should be logged.
    - apply this maximum length to all text message providers
- if `imsg` is not installed it should be a per-send failure and should log an error message indicating that `imsg` is not installed.  The monitor should still load and run, but the text message will not be sent.
- `--simulate` should not send text messages, but should log a message indicating that the text message would have been sent and what provider would have been used.
- add the twilio pip package as a dependency in `pyproject.toml` and use it to implement the `TwilioSender` class.
- the text provider should be a single global choice for the whole `settings.toml`

## imsg usage
```
  imsg send --to +14155551212 --text "hi" --service imessage
  imsg send --to +14155551212 --text "hi" --file ~/Desktop/pic.jpg --service imessage
```

---

## Implementation summary

Claude session: `4f263e05-ebac-49d6-8181-58f8b1925a81`

- `locallib/contacts.py`: added `TEXT_CHANNEL` ("text") to `CHANNELS`, and
  `_parse_text()`, which validates `text:<phone_number>` against a permissive
  E.164-style pattern (optional leading `+`, digits, spaces, dashes,
  parentheses; 7-15 digits once those are stripped) - an invalid number fails
  the monitor's load the same way a bad email/slack/teams contact does.
- `locallib/text_sender.py` (new): `TextSender` ABC with `send(target,
  subject, body)`; a shared `_cap()` truncates any body over
  `MAX_TEXT_LENGTH` (160) and logs a warning, applied uniformly regardless of
  provider.
  - `ImsgSender` shells out to `imsg send --to <target> --text <body>
    --service imessage`. Both "not on a mac" (`platform.system() !=
    "Darwin"`) and "imsg not installed" (`FileNotFoundError` from
    `subprocess.run`) are per-send failures - raised and caught by the
    notifier, logged, the monitor keeps running.
  - `TwilioSender` uses the `twilio` pip package's `Client.messages.create()`
    to send an sms; a `TwilioRestException` becomes a per-send failure the
    same way. It takes the `twilio.rest.Client` as a constructor argument
    (built by `dependencies.py`, like `HttpClient` is for the chat senders)
    rather than constructing its own.
  - `LoggingTextSender` is the `--simulate` / no-provider-configured
    variant, mirroring `LoggingChatSender` - it logs what would have been
    sent and which provider would have been used.
- `locallib/notifier.py`: `Notifier` takes a new `text_sender: TextSender |
  None` (a single sender, not a dict, since the provider is one global
  choice). `_deliver()` routes `text` contacts through a new `_send_text()`
  path, same shape as `_send_chat()`.
- `locallib/dependencies.py`: added `MonitorSettings.text: dict` and
  `get_text_sender()` - reads `settings.text.get("provider")` ("imsg" or
  "twilio"; empty/unset falls back to `LoggingTextSender`), builds
  `TwilioSettings` from `[text.twilio]` plus the auth token from
  `passwords.twilio` in `secrets.toml`. Mirroring `get_slack_sender()` /
  `get_teams_sender()`, an unconfigured twilio provider (missing
  `account_sid`/`auth_token`/`from_number`, checked via
  `TwilioSettings.is_configured`) falls back to `LoggingTextSender` instead
  of building a `TwilioSender` that would fail on every real send; wired into
  `get_notifier()`. `--simulate` always uses `LoggingTextSender` regardless
  of provider.
- `pyproject.toml`: added `twilio` as a dependency.
- `locallib/message_templates.py`: added `TEXT_CHANNEL: MAX_TEXT_LENGTH` to
  `DEFAULT_BODY_LIMITS` (and `text = 160` to `settings.toml`
  `[default.contact.limits]`) so the template-render cap for `text` matches
  the sender's own truncation length instead of silently falling back to
  email's 100,000 char limit.
- `settings.toml`, `secrets.sample.toml`, `monitors/sample.toml`,
  `docs/monitors.md`: documented the `[text]` / `[text.twilio]` sections, the
  `twilio` secret, and the `text:<phone_number>` contact syntax.
- Tests: `tests/unit/test_text_sender.py` (new - mocks `subprocess.run` and
  the Twilio client, no network or shell calls), `tests/unit/test_contacts.py`,
  `tests/unit/test_notifier.py`.
