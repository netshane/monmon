import os
from math import ceil

from loguru import logger
from nscomponents import SettingsBase, nslogging as mlog
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

from .aws_service import AwsService
from .chat_sender import (
    ChatSender,
    LoggingChatSender,
    SlackSender,
    SlackSettings,
    TeamsSender,
    TeamsSettings,
)
from .contacts import (
    CHANNELS,
    CONTACT_TYPES,
    FILE_APPEND_CHANNEL,
    FILE_CHANNEL,
    SLACK_CHANNEL,
    TEAMS_CHANNEL,
)
from .text_sender import (
    ImsgSender,
    LoggingTextSender,
    TextSender,
    TwilioSender,
    TwilioSettings,
)
from .message_templates import DEFAULT_SUBJECT_LIMIT, MessageTemplateFactory
from .custom_test_loader import CustomTestLoader
from .email_quota import EmailQuota, EmailQuotaGate
from .helpers import parse_interval
from .email_sender import (
    EmailSender,
    LoggingEmailSender,
    QuotaLimitedEmailSender,
    SmtpEmailSender,
    SmtpSettings,
)
from .file_sender import FileSender, LoggingFileSender
from .http_client import HttpClient
from .json_extractor import JsonExtractor
from .monitor_loader import MonitorLoader
from .monitor_repository import MonitorRepository
from .monitor_runner import MonitorRunner
from .monitor_service import MonitorService
from .monitor_test_factory import MonitorTestFactory
from .notifier import Notifier
from .notify_throttle import NotifyThrottle, NotifyThrottleGate
from .report_config_loader import ReportConfigLoader
from .report_data_builder import ReportDataBuilder
from .report_generator import ReportGenerator
from .report_renderer import ReportRenderer
from .opensearch_service import OpenSearchServiceFactory
from .ping_client import PingClient
from .connection_inspector import ConnectionInspector
from .db_connection_factory import DbConnectionFactory
from .docker_service import DockerServiceFactory, local_docker_host
from .schedule_calculator import RECHECK_STATUSES, ScheduleCalculator
from .value_expander import ValueExpander


class MonitorSettings(SettingsBase):
    appname: str = "monmon"
    logging: dict = {}

    aws_region: str | None = "us-east-1"

    connections: dict = {}
    passwords: dict = {}
    force_env: str | None = None

    # monitor definitions and custom test modules
    monitors_path: str = "./monitors"
    custom_tests_path: str = "./custom_tests"
    monitor_template_names: list = []

    # html report definitions, and where generated pages are written
    reports_path: str = "./reports"
    reports_output_path: str = "./output"

    # where run history is stored - swap for a postgres url when ready
    results_db: str = "sqlite:///monitors.db"

    # [contact.message.<type>.<channel>] notification templates, and the
    # [contact.limits] length caps applied to what they render
    contact: dict = {}

    # [schedule] recheck defaults, overridable per monitor
    schedule: dict = {}

    email: dict = {}
    slack: dict = {}
    teams: dict = {}
    text: dict = {}
    ping: dict = {}
    http: dict = {}
    docker: dict = {}

    def standard_logging(self):
        mlog.init_logging(application=self.appname, **self.logging)
        mlog.init_file_logging(application=self.appname, **self.logging)

    def console_logging(self):
        mlog.init_logging(application=self.appname, **self.logging)


__cached_settings = None
settings_options = {
    "prd": False,
    "dev": False,
    "force_env": None,
    "settings_file": "./settings.toml",
    "settings_extension_file": None,
}


# Path-valued settings that are conventionally relative.  When one of these
# is set from the `--extend` settings file, it should be resolved relative to
# that file's own directory rather than the application working directory.
_EXTEND_RELATIVE_PATH_FIELDS = (
    "monitors_path",
    "custom_tests_path",
    "reports_path",
    "reports_output_path",
)


def _toml_defined_keys(toml_file: str, environment: str) -> set:
    """Top-level setting keys `toml_file` defines for `environment`, read in
    isolation from the base/secrets/extend/local layers `SettingsBase.from_toml`
    merges - used only to tell which settings actually came from that file."""
    import toml

    if not os.path.isfile(toml_file):
        return set()

    raw = toml.load(toml_file)
    return set(raw.get("default", {})) | set(raw.get(environment, {}))


def _rebase_relative_path(value: str, base_dir: str) -> str:
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(base_dir, value))


def _resolve_extend_relative_paths(
    settings: MonitorSettings,
    extend_settings_file: str | None,
    environment: str,
    local_settings_file: str = "./settings.local.toml",
) -> None:
    if not extend_settings_file:
        return

    extend_dir = os.path.dirname(os.path.abspath(extend_settings_file))
    extend_keys = _toml_defined_keys(extend_settings_file, environment)
    # SettingsBase.from_toml merges base -> secret -> extend -> local, so a key
    # also set in the local file takes precedence over the extend file's value
    # and should keep resolving relative to the app working directory, as it
    # always has.
    local_keys = _toml_defined_keys(local_settings_file, environment)
    extend_keys -= local_keys

    for field in _EXTEND_RELATIVE_PATH_FIELDS:
        if field in extend_keys:
            setattr(
                settings,
                field,
                _rebase_relative_path(getattr(settings, field), extend_dir),
            )

    # results_db is a SQLAlchemy url (e.g. "sqlite:///./monitors.db"); only
    # the file portion of a sqlite url is a path that can be rebased, and
    # SQLAlchemy's "sqlite:///:memory:" in-memory convention isn't a path at all.
    if "results_db" in extend_keys and settings.results_db.startswith("sqlite:///"):
        db_path = settings.results_db[len("sqlite:///") :]
        if db_path != ":memory:":
            settings.results_db = "sqlite:///" + _rebase_relative_path(
                db_path, extend_dir
            )


def get_settings() -> MonitorSettings:
    global __cached_settings, settings_options

    if __cached_settings is not None:
        return __cached_settings

    load_dotenv()  # Load environment variables from .env file if it exists

    # if extended settings are used, pull secrets from there
    # and load any .env file in the same directory as the extended settings
    extend_settings_file = settings_options["settings_extension_file"]
    secrets_file = "./secrets.toml"
    if extend_settings_file:
        secrets_file = os.path.join(
            os.path.dirname(extend_settings_file), "secrets.toml"
        )
        extra_env_path = os.path.join(os.path.dirname(extend_settings_file), ".env")
        if os.path.exists(extra_env_path):
            load_dotenv(extra_env_path)

    if settings_options["dev"]:
        environment = settings_options["force_env"]
        __cached_settings = MonitorSettings.from_toml(
            toml_file=settings_options["settings_file"],
            environment_var=environment,
            default_env=environment,
            extend_settings_file=extend_settings_file,
            secrets_file=secrets_file,
        )
        logger.info("DEVELOPMENT environment set")
    elif settings_options["prd"]:
        environment = settings_options["force_env"]
        __cached_settings = MonitorSettings.from_toml(
            toml_file=settings_options["settings_file"],
            environment_var=environment,
            default_env=environment,
            extend_settings_file=extend_settings_file,
            secrets_file=secrets_file,
        )
        logger.info("PRODUCTION environment forced")
    else:
        environment = os.getenv("ASPNETCORE_ENVIRONMENT")
        if environment is None:
            raise RuntimeError(
                "ASPNETCORE_ENVIRONMENT environment variable is not set.  Please set it to 'Development' or 'Production'."
            )

        __cached_settings = MonitorSettings.from_toml(
            toml_file=settings_options["settings_file"],
            environment_var=environment,
            default_env=environment,
            extend_settings_file=extend_settings_file,
            secrets_file=secrets_file,
        )
        logger.info(f"DEFAULT environment set, running in {environment}")

    _resolve_extend_relative_paths(__cached_settings, extend_settings_file, environment)

    return __cached_settings


def set_settings_file(filepath: str) -> None:
    if not os.path.exists(filepath):
        raise RuntimeError(f"Invalid settings file {filepath}.  File not found")

    settings_options["settings_file"] = filepath


def set_settings_extenstion_file(filepath: str) -> None:
    if not os.path.exists(filepath):
        raise RuntimeError(f"Invalid settings file {filepath}.  File not found")

    settings_options["settings_extension_file"] = filepath


def force_environment(dev: bool = False, prd: bool = False) -> None:
    global __cached_settings, settings_options

    __cached_settings = None

    if prd:
        settings_options["force_env"] = "Production"
        settings_options["prd"] = True
    if dev:
        settings_options["force_env"] = "Development"
        settings_options["dev"] = True


def standard_logging():
    settings = get_settings()
    settings.standard_logging()


def console_logging():
    settings = get_settings()
    settings.console_logging()


def _connections_with_results_db(settings: MonitorSettings) -> dict:
    """`[connections]` with `results_db` predefined from the `results_db` url.

    An explicit `results_db` entry in `[connections]` wins; otherwise the
    run-history database is reachable from any monitor as `results_db`.
    """
    connections = dict(settings.connections)
    connections.setdefault("results_db", settings.results_db)
    return connections


def get_db_factory() -> DbConnectionFactory:
    settings = get_settings()
    return DbConnectionFactory(
        connections=_connections_with_results_db(settings),
        passwords=settings.passwords,
    )


def get_connection_inspector() -> ConnectionInspector:
    settings = get_settings()
    return ConnectionInspector(connections=_connections_with_results_db(settings))


def get_docker_service_factory() -> DockerServiceFactory:
    """Named docker connections - the `docker_host` tables in `[connections]`.

    A test naming no connection gets `[docker] host`, which defaults to the
    local docker engine for the platform - the socket on linux / macos, the
    named pipe on windows.
    """
    settings = get_settings()
    return DockerServiceFactory(
        connections=settings.connections,
        default_docker_host=settings.docker.get("host") or local_docker_host(),
        timeout_seconds=int(settings.docker.get("timeout_seconds", 30)),
    )


def get_aws_service() -> AwsService:
    settings = get_settings()
    aws_region = settings.aws_region
    if not aws_region:
        raise ValueError("AWS region is not set in settings")
    return AwsService(region=aws_region)


def get_opensearch_service_factory() -> OpenSearchServiceFactory:
    settings = get_settings()
    aws_service = get_aws_service()
    return OpenSearchServiceFactory(
        aws_service=aws_service,
        connections=settings.connections,
    )


def get_message_template_factory() -> MessageTemplateFactory:
    settings = get_settings()
    limits = settings.contact.get("limits") or {}

    return MessageTemplateFactory(
        settings_templates=settings.contact.get("message") or {},
        subject_limit=int(limits.get("subject", DEFAULT_SUBJECT_LIMIT)),
        body_limits={
            channel: int(limits[channel])
            for channel in CHANNELS
            if limits.get(channel) is not None
        },
    )


def get_monitor_loader() -> MonitorLoader:
    settings = get_settings()
    return MonitorLoader(
        monitors_path=settings.monitors_path,
        template_names=tuple(settings.monitor_template_names),
        message_template_factory=get_message_template_factory(),
    )


def get_value_expander() -> ValueExpander:
    return ValueExpander()


def get_json_extractor() -> JsonExtractor:
    return JsonExtractor()


def get_schedule_calculator() -> ScheduleCalculator:
    settings = get_settings()

    return ScheduleCalculator(
        recheck_delays={
            status: _recheck_default(settings, status) for status in RECHECK_STATUSES
        },
        recheck_max_attempts=_recheck_max_attempts(settings),
    )


def _recheck_max_attempts(settings: MonitorSettings) -> int:
    """The `[schedule] recheck_max_attempts` cap, 0 when unset or unreadable -
    a bad value must not take every cli command down with it."""
    raw = settings.schedule.get("recheck_max_attempts")
    if raw is None:
        return 0

    try:
        return max(int(float(str(raw).strip())), 0)
    except (ValueError, OverflowError):
        logger.error(
            f"Invalid [schedule] recheck_max_attempts '{raw}' - "
            "rechecks will not be capped"
        )
        return 0


def _recheck_default(settings: MonitorSettings, status: str):
    """One `[schedule] recheck_after_<status>` default, None when unset or
    unparseable - a bad interval turns the recheck off rather than failing."""
    key = f"recheck_after_{status}"
    raw = settings.schedule.get(key)
    if raw is None:
        return None

    delay = ScheduleCalculator.parse_interval(raw)
    if delay is None:
        logger.error(f"Invalid [schedule] {key} interval '{raw}' - ignoring")

    return delay


def get_notify_throttle() -> NotifyThrottle:
    """The `[contact] renotify_after_<type>` defaults, one per contact type."""
    settings = get_settings()

    return NotifyThrottle(
        delays={
            contact_type: _renotify_default(settings, contact_type)
            for contact_type in CONTACT_TYPES
        }
    )


def _renotify_default(settings: MonitorSettings, contact_type: str):
    """One `[contact] renotify_after_<type>` default, None when unset or
    unparseable - a bad interval leaves the type unthrottled rather than
    failing, matching how a bad recheck interval is handled."""
    key = f"renotify_after_{contact_type}"
    raw = settings.contact.get(key)
    if raw is None:
        return None

    delay = parse_interval(raw)
    if delay is None:
        logger.error(f"Invalid [contact] {key} interval '{raw}' - ignoring")

    return delay


def get_http_client() -> HttpClient:
    settings = get_settings()
    return HttpClient(
        timeout_seconds=int(settings.http.get("timeout_seconds", 30)),
        verify_ssl=bool(settings.http.get("verify_ssl", True)),
    )


def get_ping_client() -> PingClient:
    settings = get_settings()
    return PingClient(
        count=int(settings.ping.get("count", 2)),
        timeout_seconds=int(settings.ping.get("timeout_seconds", 5)),
    )


def get_custom_test_loader() -> CustomTestLoader:
    settings = get_settings()
    return CustomTestLoader(custom_tests_path=settings.custom_tests_path)


def get_results_engine() -> Engine:
    """Engine for the monitor results database.

    `results_db` is a SQLAlchemy url, so moving from sqlite to postgres is a
    settings change (e.g. `postgresql+psycopg://user:pw@host/monitors`).
    """
    settings = get_settings()
    url = settings.results_db
    passwd = settings.passwords.get("results_db")
    if passwd:
        url = url.format(passwd=passwd)

    return create_engine(url, future=True)


def get_monitor_repository(create_schema: bool = True) -> MonitorRepository:
    repository = MonitorRepository(engine=get_results_engine())
    if create_schema:
        repository.create_schema()

    return repository


def get_monitor_test_factory(
    db_factory: DbConnectionFactory | None = None,
    opensearch_factory: OpenSearchServiceFactory | None = None,
    docker_factory: DockerServiceFactory | None = None,
) -> MonitorTestFactory:
    return MonitorTestFactory(
        value_expander=get_value_expander(),
        json_extractor=get_json_extractor(),
        db_factory=db_factory or get_db_factory(),
        docker_factory=docker_factory or get_docker_service_factory(),
        opensearch_factory=opensearch_factory or get_opensearch_service_factory(),
        http_client=get_http_client(),
        ping_client=get_ping_client(),
        custom_test_loader=get_custom_test_loader(),
    )


def get_monitor_runner(simulate: bool = False) -> MonitorRunner:
    db_factory = get_db_factory()
    opensearch_factory = get_opensearch_service_factory()
    docker_factory = get_docker_service_factory()

    return MonitorRunner(
        test_factory=get_monitor_test_factory(
            db_factory, opensearch_factory, docker_factory
        ),
        db_factory=db_factory,
        docker_factory=docker_factory,
        opensearch_factory=opensearch_factory,
        simulate=simulate,
    )


def get_email_quota() -> EmailQuota:
    """The cross run send limits.  Unset keys leave a limit off."""
    settings = get_settings()

    return EmailQuota(
        max_per_day=int(settings.email.get("max_per_day", 0) or 0),
        max_per_recipient=int(settings.email.get("max_per_recipient", 0) or 0),
        cooldown_window_minutes=int(
            settings.email.get("cooldown_window_minutes", 60) or 60
        ),
    )


def get_email_send_log_retention_days() -> int:
    """How long `email_send_log` rows are kept by `purge`.

    Purging inside a limit's window would reset it - a recipient over its
    cooldown becomes sendable again the moment its rows are deleted - so a
    retention shorter than the longest configured window is raised to it.
    """
    settings = get_settings()
    configured = int(settings.email.get("send_log_retention_days", 7) or 7)

    quota = get_email_quota()
    required = 1 if quota.max_per_day > 0 else 0
    if quota.max_per_recipient > 0:
        required = max(required, ceil(quota.cooldown_window_minutes / (60 * 24)))

    if configured < required:
        logger.warning(
            f"email.send_log_retention_days ({configured}) is shorter than the "
            f"configured send quota window - keeping {required} day(s) of send "
            f"log instead so purge does not reset a limit"
        )
        return required

    return configured


def get_email_sender(simulate: bool = False) -> EmailSender:
    settings = get_settings()
    host = settings.email.get("host")

    if simulate or not host:
        if not simulate:
            logger.debug("No email host configured - emails will be logged only")
        return LoggingEmailSender(prefix="SIMULATED EMAIL" if simulate else "EMAIL")

    sender = SmtpEmailSender(
        SmtpSettings(
            host=host,
            port=int(settings.email.get("port", 25)),
            from_address=settings.email.get("from_address", "monitors@localhost"),
            username=settings.email.get("username"),
            password=settings.passwords.get("email") or settings.email.get("password"),
            use_tls=bool(settings.email.get("use_tls", False)),
            timeout_seconds=int(settings.email.get("timeout_seconds", 30)),
        )
    )

    # only a live sender is gated - a simulated run must not consume quota or
    # write to the send log
    quota = get_email_quota()
    if not quota.enabled:
        return sender

    return QuotaLimitedEmailSender(
        inner=sender,
        gate=EmailQuotaGate(repository=get_monitor_repository(), quota=quota),
    )


def get_notification_http_client() -> HttpClient:
    """Http client used to reach slack / teams.

    Deliberately not `get_http_client()` - `http.verify_ssl` is there so a
    monitor can probe a host with a self signed certificate, and that must
    never disable verification on a request carrying a chat credential.
    """
    settings = get_settings()
    return HttpClient(
        timeout_seconds=int(settings.http.get("timeout_seconds", 30)),
        verify_ssl=True,
    )


def get_slack_sender(simulate: bool = False) -> ChatSender:
    settings = get_settings()
    slack_settings = SlackSettings(
        token=settings.passwords.get("slack") or settings.slack.get("token"),
        webhook_url=settings.slack.get("webhook_url"),
        webhooks=dict(settings.slack.get("webhooks") or {}),
    )

    if simulate or not slack_settings.is_configured:
        if not simulate:
            logger.debug(
                "No slack credentials configured - messages will be logged only"
            )
        return LoggingChatSender(
            channel=SLACK_CHANNEL,
            prefix="SIMULATED SLACK" if simulate else "SLACK",
        )

    return SlackSender(
        settings=slack_settings, http_client=get_notification_http_client()
    )


def get_teams_sender(simulate: bool = False) -> ChatSender:
    settings = get_settings()
    teams_settings = TeamsSettings(
        webhook_url=settings.teams.get("webhook_url"),
        webhooks=dict(settings.teams.get("webhooks") or {}),
    )

    if simulate or not teams_settings.is_configured:
        if not simulate:
            logger.debug("No teams webhook configured - messages will be logged only")
        return LoggingChatSender(
            channel=TEAMS_CHANNEL,
            prefix="SIMULATED TEAMS" if simulate else "TEAMS",
        )

    return TeamsSender(
        settings=teams_settings, http_client=get_notification_http_client()
    )


def get_chat_senders(simulate: bool = False) -> dict[str, ChatSender]:
    return {
        SLACK_CHANNEL: get_slack_sender(simulate=simulate),
        TEAMS_CHANNEL: get_teams_sender(simulate=simulate),
    }


def get_file_sender(append: bool = False, simulate: bool = False) -> FileSender:
    if simulate:
        prefix = "SIMULATED FILE_APPEND" if append else "SIMULATED FILE"
        return LoggingFileSender(append=append, prefix=prefix)

    return FileSender(append=append)


def get_file_senders(simulate: bool = False) -> dict[str, FileSender]:
    return {
        FILE_CHANNEL: get_file_sender(append=False, simulate=simulate),
        FILE_APPEND_CHANNEL: get_file_sender(append=True, simulate=simulate),
    }


def get_text_sender(simulate: bool = False) -> TextSender:
    settings = get_settings()
    provider = settings.text.get("provider")

    if simulate or not provider:
        if not simulate:
            logger.debug("No text provider configured - texts will be logged only")
        return LoggingTextSender(provider=provider or "text")

    if provider == "imsg":
        return ImsgSender()

    if provider == "twilio":
        twilio = settings.text.get("twilio") or {}
        twilio_settings = TwilioSettings(
            account_sid=twilio.get("account_sid", ""),
            auth_token=settings.passwords.get("twilio") or twilio.get("auth_token", ""),
            from_number=twilio.get("from_number", ""),
        )

        if not twilio_settings.is_configured:
            logger.debug("No twilio credentials configured - texts will be logged only")
            return LoggingTextSender(provider="twilio")

        return TwilioSender(
            twilio_settings,
            client=TwilioClient(
                twilio_settings.account_sid, twilio_settings.auth_token
            ),
        )

    raise ValueError(
        f"Unknown text.provider '{provider}' - expected one of 'imsg', 'twilio'"
    )


def get_notifier(simulate: bool = False) -> Notifier:
    settings = get_settings()
    return Notifier(
        email_sender=get_email_sender(simulate=simulate),
        chat_senders=get_chat_senders(simulate=simulate),
        file_senders=get_file_senders(simulate=simulate),
        text_sender=get_text_sender(simulate=simulate),
        subject_prefix=settings.email.get("subject_prefix", "[monitor]"),
        # batch subjects come from settings.toml only, so the notifier holds
        # the settings level set rather than any one monitor's
        templates=get_message_template_factory().build(),
        # a simulated run must never consume or advance a renotify window
        throttle_gate=None if simulate else get_notify_throttle_gate(),
    )


def get_notify_throttle_gate() -> NotifyThrottleGate:
    return NotifyThrottleGate(
        repository=get_monitor_repository(),
        throttle=get_notify_throttle(),
    )


def get_report_config_loader() -> ReportConfigLoader:
    settings = get_settings()
    return ReportConfigLoader(reports_path=settings.reports_path)


def get_report_data_builder() -> ReportDataBuilder:
    return ReportDataBuilder(
        repository=get_monitor_repository(),
        monitor_loader=get_monitor_loader(),
    )


def get_report_renderer(strict: bool = False) -> ReportRenderer:
    return ReportRenderer(strict=strict)


def get_report_generator(
    output_path: str | None = None, strict: bool = False
) -> ReportGenerator:
    settings = get_settings()
    return ReportGenerator(
        config_loader=get_report_config_loader(),
        data_builder=get_report_data_builder(),
        renderer=get_report_renderer(strict=strict),
        output_path=output_path or settings.reports_output_path,
    )


def get_monitor_service(simulate: bool = False) -> MonitorService:
    return MonitorService(
        loader=get_monitor_loader(),
        runner=get_monitor_runner(simulate=simulate),
        repository=get_monitor_repository(),
        schedule_calculator=get_schedule_calculator(),
        notifier=get_notifier(simulate=simulate),
        persist_results=not simulate,
    )
