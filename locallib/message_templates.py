"""Jinja2 templates for notification messages.

A template is configured as `[contact.message.<type>.<channel>]`, e.g.

    [contact.message.alert.email]
    subject = "alert: {{ name }}"
    body = \"\"\"...\"\"\"

Templates resolve field by field: a monitor's own `[contact.message]` section
wins over the defaults in `settings.toml`, which win over the built in
defaults below.  A monitor that only overrides `body` therefore keeps the
`subject` it inherited.

`batch_subject` is the subject of a combined (`combine_alerts` /
`combine_reports`) message and may only be set in `settings.toml`.
"""

from __future__ import annotations

from dataclasses import dataclass

from jinja2 import ChainableUndefined, Environment, Template, TemplateError
from markupsafe import Markup

from .contacts import (
    CHANNELS,
    CONTACT_TYPES,
    EMAIL_CHANNEL,
    FILE_CHANNELS,
    SLACK_CHANNEL,
    TEAMS_CHANNEL,
    TEXT_CHANNEL,
)
from .helpers import format_html_table, format_table
from .text_sender import MAX_TEXT_LENGTH

# an html alternative is meaningful on email (an html capable mail client) and
# on the file channels (a target ending in `.html`) - chat channels only ever
# deliver plain text
HTML_CHANNELS = (EMAIL_CHANNEL, *FILE_CHANNELS)

# what an undefined variable renders as - templates are written by hand and a
# missing variable should not lose the whole notification
UNDEFINED_TEXT = "(Undefined)"

ELLIPSIS = "..."

# `message` is the natural name for a chat body, `body` for an email one -
# both are accepted everywhere and stored as `body`
BODY_KEYS = ("body", "message")
TEMPLATE_KEYS = ("subject", "batch_subject", "html", *BODY_KEYS)

# rendered output is capped so a runaway query cannot produce a message the
# channel will reject outright
DEFAULT_SUBJECT_LIMIT = 200
DEFAULT_BODY_LIMITS = {
    EMAIL_CHANNEL: 100_000,
    SLACK_CHANNEL: 3_900,
    TEAMS_CHANNEL: 20_000,
    TEXT_CHANNEL: MAX_TEXT_LENGTH,
}


class MessageTemplateError(ValueError):
    """Raised when a message template is misconfigured or will not compile."""


class LenientUndefined(ChainableUndefined):
    """Undefined that renders as `(Undefined)` instead of raising.

    `ChainableUndefined` keeps `{{ alert.value }}` working when `alert` itself
    was never provided, which is the common case for a template shared across
    contact types.
    """

    __slots__ = ()

    def __str__(self) -> str:
        return UNDEFINED_TEXT

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False


@dataclass
class RenderedMessage:
    """One rendered notification, ready to hand to a sender."""

    subject: str
    body: str
    html: str | None = None


def _report_table(report) -> str:
    return format_table(getattr(report, "columns", []), getattr(report, "rows", []))


def _report_html_table(report) -> Markup:
    return Markup(
        format_html_table(getattr(report, "columns", []), getattr(report, "rows", []))
    )


def _format_datetime(value, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if value is None or isinstance(value, LenientUndefined):
        return ""
    try:
        return value.strftime(fmt)
    except AttributeError:
        return str(value)


def build_environment(autoescape: bool) -> Environment:
    """An environment for one escaping mode.

    Plain text bodies must not be escaped - `&` in a table cell should stay a
    `&`.  Html bodies and chat messages are escaped so a value coming out of a
    monitored system cannot inject markup.
    """
    environment = Environment(
        autoescape=autoescape,
        undefined=LenientUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    environment.filters.update(
        {
            "table": _report_table,
            "html_table": _report_html_table,
            "datetime": _format_datetime,
        }
    )

    return environment


# -- built in defaults ----------------------------------------------------

_ALERT_TEXT = """\
[{{ name }}] {{ alert.message }}
{% if alert.name and alert.name != alert.message %}
  name: {{ alert.name }}
{% endif %}
{% if alert.value is not none %}
  value: {{ alert.value }}
{% endif %}
{% if alert.threshold is not none %}
  threshold: {{ alert.threshold }}
{% endif %}
{% if alert.details %}
  details: {{ alert.details }}
{% endif %}
{% if description %}
Monitor: {{ description }}
{% endif %}
{% if link %}
Support: {{ link }}
{% endif %}
"""

_REPORT_TEXT = """\
{% if test_results %}
[{{ name }}] tests:
{% for test in test_results %}
  {{ test.key }} ({{ test.test_type }}): {{ test.status.value }}\
{% if test.message %} - {{ test.message }}{% endif %}
{% if link %}     Support: {{ link }}{% endif %}
     {{ finished_at | datetime }} - {{ '%.2f' | format(test.duration_seconds) }}s


{% endfor %}
{% endif %}
{% for report in reports %}

[{{ name }}] {{ report.title }} - {{ report.row_count }} row(s)

{{ report | table }}
{% endfor %}
"""

_REPORT_HTML = """\
{% if test_results %}
<h3>{{ name }} - tests</h3>
<ul>
{% for test in test_results %}
<li>{{ test.key }} ({{ test.test_type }}): {{ test.status.value }}\
{% if test.message %} - {{ test.message }}{% endif %}
{% if link %}<br> &nbsp; <a href="{{ link }}">Support</a>{% endif %}
<br> &nbsp; {{ started_at | datetime }} - {{ '%.2f' | format(test.duration_seconds) }}s
{% if link %} &nbsp; <a href="{{ link }}">Support</a>{% endif %}</li>
{% endfor %}
</ul>
{% endif %}
{% for report in reports %}
<h3>{{ name }} - {{ report.title }}</h3>
{{ report | html_table }}
{% endfor %}
"""

_INFO_TEXT = """\
Monitor: {{ name }}
Status: {{ status }}
Started: {{ started_at | datetime }}
Duration: {{ '%.2f' | format(duration_seconds) }}s

{% for test in test_results %}
{{ test.key }} ({{ test.test_type }}): {{ test.status.value }}\
{% if test.message %} - {{ test.message }}{% endif %}

{% endfor %}
{% for report in reports %}

[{{ name }}] {{ report.title }} - {{ report.row_count }} row(s)

{{ report | table }}
{% endfor %}
"""

_ERROR_TEXT = """\
Monitor '{{ name }}' reported errors:

{% for test in test_results %}
{% if test.error %}
{{ test.key }}: {{ test.error }}
{% endif %}
{% endfor %}
"""

_NOTIFY_TEXT = (
    "Monitor '{{ name }}' ran at {{ started_at | datetime }} with status {{ status }}."
)

_ALERT_BATCH_SUBJECT = (
    "alerts: {{ alert_count }} monitor(s) - {{ monitor_names | join(', ') }}"
)
_REPORT_BATCH_SUBJECT = (
    "reports: {{ report_count }} monitor(s) - {{ monitor_names | join(', ') }}"
)

_DEFAULT_BY_TYPE: dict[str, dict[str, str]] = {
    "alert": {
        "subject": "alert: {{ name }} - {{ alert.name or alert.message }}",
        "batch_subject": _ALERT_BATCH_SUBJECT,
        "body": _ALERT_TEXT,
    },
    "report": {
        "subject": "report: {{ name }} - {{ reports | length }} report(s)",
        "batch_subject": _REPORT_BATCH_SUBJECT,
        "body": _REPORT_TEXT,
        "html": _REPORT_HTML,
    },
    "info": {
        "subject": "info: {{ name }} ({{ status }})",
        "batch_subject": "info: {{ monitor_count }} monitor(s)",
        "body": _INFO_TEXT,
    },
    "notify": {
        "subject": "ran: {{ name }} ({{ status }})",
        "batch_subject": "ran: {{ monitor_count }} monitor(s)",
        "body": _NOTIFY_TEXT,
    },
    "error": {
        "subject": "error: {{ name }}",
        "batch_subject": "errors: {{ monitor_count }} monitor(s)",
        "body": _ERROR_TEXT,
    },
}


def default_templates() -> dict[str, dict[str, dict[str, str]]]:
    """The built in template for every contact type and channel.

    Only `HTML_CHANNELS` carry an `html` alternative - the chat channels take
    plain text only, so an `html` template there would never be delivered.
    """
    return {
        contact_type: {
            channel: {
                key: value
                for key, value in fields.items()
                if key != "html" or channel in HTML_CHANNELS
            }
            for channel in CHANNELS
        }
        for contact_type, fields in _DEFAULT_BY_TYPE.items()
    }


# -- compiled templates ---------------------------------------------------


@dataclass(frozen=True)
class _Compiled:
    """The compiled templates for one contact type and channel."""

    subject: Template
    body: Template
    batch_subject: Template
    html: Template | None = None


class MessageTemplateSet:
    """Every compiled message template for one monitor."""

    def __init__(
        self,
        compiled: dict[tuple[str, str], _Compiled],
        subject_limit: int = DEFAULT_SUBJECT_LIMIT,
        body_limits: dict[str, int] | None = None,
    ):
        self._compiled = compiled
        self.subject_limit = subject_limit
        self.body_limits = body_limits or dict(DEFAULT_BODY_LIMITS)

    def has_html(self, contact_type: str, channel: str) -> bool:
        compiled = self._compiled.get((contact_type, channel))
        return compiled is not None and compiled.html is not None

    def render(self, contact_type: str, channel: str, context: dict) -> RenderedMessage:
        """Render the message for one contact type and channel.

        Raises `MessageTemplateError` if the template fails at render time -
        the caller decides whether that loses one message or the whole run.
        """
        compiled = self._require(contact_type, channel)

        return RenderedMessage(
            subject=self._cap(
                self._render(
                    compiled.subject, context, contact_type, channel, "subject"
                ),
                self.subject_limit,
            ),
            body=self._cap(
                self._render(compiled.body, context, contact_type, channel, "body"),
                self.body_limit(channel),
            ),
            html=(
                self._cap(
                    self._render(compiled.html, context, contact_type, channel, "html"),
                    self.body_limit(channel),
                )
                if compiled.html is not None
                else None
            ),
        )

    def render_batch_subject(
        self, contact_type: str, channel: str, context: dict
    ) -> str:
        compiled = self._require(contact_type, channel)

        return self._cap(
            self._render(
                compiled.batch_subject, context, contact_type, channel, "batch_subject"
            ),
            self.subject_limit,
        )

    def body_limit(self, channel: str) -> int:
        return self.body_limits.get(channel, DEFAULT_BODY_LIMITS[EMAIL_CHANNEL])

    def cap_subject(self, text: str) -> str:
        return self._cap(text, self.subject_limit)

    def cap_body(self, channel: str, text: str) -> str:
        """Apply a channel's length cap to text assembled outside a template."""
        return self._cap(text, self.body_limit(channel))

    def _require(self, contact_type: str, channel: str) -> _Compiled:
        compiled = self._compiled.get((contact_type, channel))
        if compiled is None:
            raise MessageTemplateError(
                f"No message template for contact type '{contact_type}' on "
                f"channel '{channel}'"
            )

        return compiled

    @staticmethod
    def _render(
        template: Template, context: dict, contact_type: str, channel: str, field: str
    ) -> str:
        try:
            return template.render(**context).strip()
        except Exception as e:
            raise MessageTemplateError(
                f"Failed to render the '{field}' template for "
                f"'{contact_type}.{channel}': {e}"
            ) from e

    @staticmethod
    def _cap(text: str, limit: int) -> str:
        """Truncate an over long message, marking it as truncated."""
        if limit <= 0 or len(text) <= limit:
            return text

        return text[: max(limit - len(ELLIPSIS), 0)] + ELLIPSIS


class MessageTemplateFactory:
    """Builds a compiled `MessageTemplateSet` for a monitor.

    Compilation happens while the monitor is being loaded so a broken template
    is a load failure rather than a surprise at notification time.  Compiled
    templates are cached by source text, so the defaults shared by every
    monitor are only compiled once.
    """

    def __init__(
        self,
        settings_templates: dict | None = None,
        subject_limit: int = DEFAULT_SUBJECT_LIMIT,
        body_limits: dict[str, int] | None = None,
    ):
        self.subject_limit = subject_limit
        self.body_limits = {**DEFAULT_BODY_LIMITS, **(body_limits or {})}
        self.settings_templates = _normalize(
            settings_templates or {}, source="settings.toml"
        )
        self._cache: dict[tuple[str, bool], Template] = {}
        self._environments = {
            False: build_environment(autoescape=False),
            True: build_environment(autoescape=True),
        }

    def build(self, overrides: dict | None = None) -> MessageTemplateSet:
        """Compile the templates for one monitor's `[contact.message]`."""
        monitor = _normalize(
            overrides or {}, source="[contact.message]", allow_batch_subject=False
        )
        defaults = default_templates()

        compiled: dict[tuple[str, str], _Compiled] = {}
        for contact_type in CONTACT_TYPES:
            for channel in CHANNELS:
                fields = {
                    **defaults.get(contact_type, {}).get(channel, {}),
                    **self.settings_templates.get(contact_type, {}).get(channel, {}),
                    **monitor.get(contact_type, {}).get(channel, {}),
                }
                compiled[(contact_type, channel)] = self._compile(
                    contact_type, channel, fields
                )

        return MessageTemplateSet(
            compiled,
            subject_limit=self.subject_limit,
            body_limits=dict(self.body_limits),
        )

    def _compile(self, contact_type: str, channel: str, fields: dict) -> _Compiled:
        html = fields.get("html") if channel in HTML_CHANNELS else None

        return _Compiled(
            subject=self._template(contact_type, channel, "subject", fields, False),
            batch_subject=self._template(
                contact_type, channel, "batch_subject", fields, False
            ),
            # a plain text body (email, file) is delivered as typed; a chat
            # body ends up somewhere that interprets markup
            body=self._template(
                contact_type, channel, "body", fields, channel not in HTML_CHANNELS
            ),
            html=(
                self._template(contact_type, channel, "html", {"html": html}, True)
                if html
                else None
            ),
        )

    def _template(
        self,
        contact_type: str,
        channel: str,
        field: str,
        fields: dict,
        autoescape: bool,
    ) -> Template:
        source = fields.get(field) or ""
        key = (f"{field}\x00{source}", autoescape)

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            template = self._environments[autoescape].from_string(source)
        except TemplateError as e:
            raise MessageTemplateError(
                f"Invalid '{field}' template for '{contact_type}.{channel}': {e}"
            ) from e

        self._cache[key] = template

        return template


def _normalize(
    section: dict, source: str, allow_batch_subject: bool = True
) -> dict[str, dict[str, dict[str, str]]]:
    """Validate a `[contact.message]` section into type -> channel -> fields.

    `message` and `body` are the same field under two names, so a chat
    template can read the way a chat template should.
    """
    if not isinstance(section, dict):
        raise MessageTemplateError(f"{source}: [contact.message] must be a table")

    normalized: dict[str, dict[str, dict[str, str]]] = {}

    for contact_type, channels in section.items():
        if contact_type not in CONTACT_TYPES:
            raise MessageTemplateError(
                f"{source}: unknown message template type '{contact_type}' - "
                f"expected one of {', '.join(CONTACT_TYPES)}"
            )
        if not isinstance(channels, dict):
            raise MessageTemplateError(
                f"{source}: [contact.message.{contact_type}] must hold one "
                f"table per channel, e.g. [contact.message.{contact_type}.email]"
            )

        for channel, fields in channels.items():
            if channel not in CHANNELS:
                raise MessageTemplateError(
                    f"{source}: unknown message template channel "
                    f"'{contact_type}.{channel}' - "
                    f"expected one of {', '.join(CHANNELS)}"
                )
            if not isinstance(fields, dict):
                raise MessageTemplateError(
                    f"{source}: [contact.message.{contact_type}.{channel}] "
                    f"must be a table of template strings"
                )

            normalized.setdefault(contact_type, {})[channel] = _normalize_fields(
                contact_type, channel, fields, source, allow_batch_subject
            )

    return normalized


def _normalize_fields(
    contact_type: str,
    channel: str,
    fields: dict,
    source: str,
    allow_batch_subject: bool,
) -> dict[str, str]:
    where = f"contact.message.{contact_type}.{channel}"
    result: dict[str, str] = {}

    for key, value in fields.items():
        if key not in TEMPLATE_KEYS:
            raise MessageTemplateError(
                f"{source}: unknown setting '{key}' in [{where}] - "
                f"expected one of {', '.join(TEMPLATE_KEYS)}"
            )
        if key == "batch_subject" and not allow_batch_subject:
            raise MessageTemplateError(
                f"{source}: 'batch_subject' in [{where}] may only be set in "
                f"settings.toml, not in a monitor"
            )
        if key == "html" and channel not in HTML_CHANNELS:
            raise MessageTemplateError(
                f"{source}: 'html' in [{where}] is only supported on the "
                f"{', '.join(HTML_CHANNELS)} channels"
            )
        if not isinstance(value, str):
            raise MessageTemplateError(
                f"{source}: '{key}' in [{where}] must be a template string"
            )

        # an empty string means "inherit", so it never masks the default
        if not value.strip():
            continue

        result["body" if key in BODY_KEYS else key] = value

    return result
