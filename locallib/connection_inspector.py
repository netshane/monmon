"""Read-only view of the connections defined in settings.

Backs the `connections` cli command.  A connection value is either a database
url string, a table naming a `docker_host`, or an opensearch table - the same
classification `MonitorRunner._register_connections` uses.  Secrets are redacted
for display; the `{passwd}` placeholder is left intact because it is not itself
a secret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PASSWD_PLACEHOLDER = "{passwd}"

# keys inside a docker / opensearch connection table whose value is a secret
_SECRET_TABLE_KEYS = {
    "password",
    "passwd",
    "secret",
    "secret_key",
    "token",
    "api_key",
    "aws_secret_access_key",
}

# `pwd=` / `password=` style tokens in an odbc / dsn connection string or a
# url query string.  The value is a `{...}` braced token (which SQL Server uses
# to quote passwords containing `;`) or a run of non-delimiter characters.
_ODBC_SECRET = re.compile(
    r"(?i)(?<![A-Za-z])(pwd|passwd|password)=(\{[^}]*\}|[^;&\s]*)"
)

# `scheme://user:password@host...` - matched up to the last `@` of the authority
# so a password containing an unescaped `@` is still fully covered.
_URL_USERINFO = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/?#]*)@")

REDACTED = "***"


@dataclass
class ConnectionInfo:
    name: str
    kind: str  # "db" | "docker" | "opensearch"
    connection: str


def _redact_odbc(text: str) -> str:
    def repl(match: re.Match) -> str:
        if match.group(2).strip() == PASSWD_PLACEHOLDER:
            return match.group(0)
        return f"{match.group(1)}={REDACTED}"

    return _ODBC_SECRET.sub(repl, text)


def _redact_userinfo(match: re.Match) -> str:
    scheme, userinfo = match.group(1), match.group(2)
    if ":" not in userinfo:
        return match.group(0)
    user, secret = userinfo.split(":", 1)
    if secret == PASSWD_PLACEHOLDER:
        return match.group(0)
    return f"{scheme}{user}:{REDACTED}@"


def redact_connection_string(value: str) -> str:
    """Redact an embedded password from a database url, keeping `{passwd}`.

    Covers both `scheme://user:pw@host` urls (including passwords with an
    unescaped `@`) and `PWD=`/`PASSWORD=` tokens in odbc/dsn strings and url
    query strings.
    """
    text = _URL_USERINFO.sub(_redact_userinfo, value, count=1)
    return _redact_odbc(text)


def _redact_table(table: dict) -> dict:
    return {
        key: (
            REDACTED
            if key.lower() in _SECRET_TABLE_KEYS and value != PASSWD_PLACEHOLDER
            else value
        )
        for key, value in table.items()
    }


class ConnectionInspector:
    def __init__(self, connections: dict):
        self.connections = dict(connections)

    def list(self) -> list[ConnectionInfo]:
        infos: list[ConnectionInfo] = []
        for name, value in sorted(self.connections.items()):
            infos.append(self._describe(name, value))
        return infos

    @staticmethod
    def _describe(name: str, value) -> ConnectionInfo:
        if isinstance(value, dict):
            if value.get("docker_host"):
                kind = "docker"
            else:
                kind = "opensearch"
            redacted = _redact_table(value)
            rendered = ", ".join(f"{k}={v}" for k, v in redacted.items())
            return ConnectionInfo(name=name, kind=kind, connection=rendered)

        return ConnectionInfo(
            name=name,
            kind="db",
            connection=redact_connection_string(str(value)),
        )
