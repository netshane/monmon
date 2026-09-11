import pytest

from locallib.connection_inspector import (
    ConnectionInspector,
    redact_connection_string,
)

"""
Test: connection_inspector - the read-only view behind the `connections` command
"""


@pytest.mark.unit
def test_lists_connections_sorted_with_kind():
    inspector = ConnectionInspector(
        {
            "results_db": "sqlite:///results.db",
            "db_prd6": "mssql+mssql-python://Server=s;Database=d;Trusted_Connection=yes",
            "search": {"host": "https://es:9200", "default_index": "logs"},
            "docker_local": {"docker_host": "unix:///var/run/docker.sock"},
        }
    )

    infos = inspector.list()

    assert [(i.name, i.kind) for i in infos] == [
        ("db_prd6", "db"),
        ("docker_local", "docker"),
        ("results_db", "db"),
        ("search", "opensearch"),
    ]


@pytest.mark.unit
def test_redacts_embedded_url_password():
    assert (
        redact_connection_string("postgresql+psycopg://user:s3cret@host/monitors")
        == "postgresql+psycopg://user:***@host/monitors"
    )


@pytest.mark.unit
def test_keeps_passwd_placeholder():
    url = "postgresql+psycopg://user:{passwd}@host/monitors"
    assert redact_connection_string(url) == url


@pytest.mark.unit
def test_redacts_odbc_password_token():
    result = redact_connection_string(
        "mssql+mssql-python://Server=s;Database=d;PWD=hunter2;Encrypt=yes"
    )
    assert "hunter2" not in result
    assert "PWD=***" in result


@pytest.mark.unit
def test_redacts_url_password_containing_unescaped_at():
    result = redact_connection_string("postgresql://user:p@ss@host/db")
    assert "ss@host" not in result
    assert result == "postgresql://user:***@host/db"


@pytest.mark.unit
def test_redacts_odbc_password_with_braces_and_semicolon():
    result = redact_connection_string(
        "mssql+mssql-python://Server=s;PWD={p;w0rd};UID=u"
    )
    assert "w0rd" not in result
    assert "PWD=***;UID=u" in result


@pytest.mark.unit
def test_query_string_password_redaction_keeps_other_params():
    result = redact_connection_string(
        "postgresql://user@host/db?password=topsecret&sslmode=require"
    )
    assert "topsecret" not in result
    assert "sslmode=require" in result


@pytest.mark.unit
def test_sqlite_url_is_unchanged():
    assert redact_connection_string("sqlite:///results.db") == "sqlite:///results.db"


@pytest.mark.unit
def test_redacts_secret_keys_in_a_table():
    inspector = ConnectionInspector(
        {"search": {"host": "https://es:9200", "token": "abc123"}}
    )

    (info,) = inspector.list()

    assert "abc123" not in info.connection
    assert "token=***" in info.connection
    assert "host=https://es:9200" in info.connection
