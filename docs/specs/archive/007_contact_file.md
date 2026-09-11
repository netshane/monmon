Create two new contact methods called "file" and "file_append" that write the notification to a file.
e.g.

```toml
[contact]
reports = [
    "file:output/report.txt",
    "file:/tmp/monitor/report.html",
    "file_append:output/report.txt",
]
```

The file path should be evaluated relative to the current working directory of the monitor process.  If the file path is not valid, the monitor should fail to load with an error message indicating the invalid contact.  To verify the file path is valid, the monitor should attempt to open the file for writing.

Add new message templates for the file and file_append contact methods.

If the file extension is ".html", the message should be written as html.  If the file extension is ".txt", the message should be written as plain text.  If the file extension is not specified, the message should be written as plain text.

If two monitors write to the same file, the messages should be written in the order that the monitors are run.  If a monitor is run multiple times, the messages should be written in the order that the monitors are run. If this results in clobbering a file that is acceptable.

Attempt to create directories that do not exist.

---

## Implementation summary

Claude session: `25e0e31e-aab9-4434-b623-af68b0a7553b`

- `locallib/contacts.py`: added `FILE_CHANNEL` ("file"), `FILE_APPEND_CHANNEL`
  ("file_append"), and `FILE_CHANNELS`; extended `CHANNELS`. Added
  `_parse_file()`, which validates a `file:`/`file_append:` target at parse
  time by creating any missing parent directories and opening the file for
  writing (`"w"` for `file`, `"a"` for `file_append` so validation does not
  clobber existing content) - any `OSError` becomes an `InvalidContactError`,
  which fails the monitor's load the same way a bad email/slack/teams contact
  does.
- `locallib/file_sender.py` (new): `FileSender.send(target, subject, body,
  html=None)` writes `html` when the target ends in `.html` and an html
  render is available, otherwise `body`; overwrites or appends per the
  `append` flag set at construction, creating missing parent directories at
  send time too. `LoggingFileSender` is the `--simulate` / no-op variant,
  mirroring `LoggingEmailSender`/`LoggingChatSender`.
- `locallib/notifier.py`: `Notifier` takes a new `file_senders` dict
  (keyed by channel). `_deliver()` now routes `file`/`file_append` contacts
  through their own `_send_file()` path instead of the chat path, so - unlike
  chat - the rendered `html` is preserved and handed to the sender.
- `locallib/message_templates.py`: added `HTML_CHANNELS = (EMAIL_CHANNEL,
  *FILE_CHANNELS)`; an `html` template field is now valid for both file
  channels (in addition to email), and their `body` field is treated as
  plain text (not autoescaped), matching email rather than chat.
- `locallib/dependencies.py`: added `get_file_sender()` / `get_file_senders()`
  and wired them into `get_notifier()`; `--simulate` swaps in
  `LoggingFileSender` so no file is touched.
- `settings.toml` / `monitors/sample.toml`: updated the `[contact]` and
  `[contact.message]` documentation comments to include the two new channels.
- Tests: `tests/unit/test_contacts.py`, `tests/unit/test_file_sender.py` (new),
  `tests/unit/test_notifier.py`, `tests/unit/test_message_templates.py`,
  `tests/unit/test_monitor_loader.py`.
