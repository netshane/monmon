import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import monmon
from locallib.connection_inspector import ConnectionInspector
from locallib.dependencies import _connections_with_results_db

"""
Test: the `connections` cli command and the results_db predefined connection
"""


def _settings(connections=None, results_db="sqlite:///results.db"):
    return SimpleNamespace(connections=connections or {}, results_db=results_db)


@pytest.mark.unit
def test_results_db_defaults_to_the_results_db_url():
    connections = _connections_with_results_db(_settings())
    assert connections["results_db"] == "sqlite:///results.db"


@pytest.mark.unit
def test_explicit_results_db_connection_wins():
    connections = _connections_with_results_db(
        _settings(connections={"results_db": "postgresql+psycopg://h/db"})
    )
    assert connections["results_db"] == "postgresql+psycopg://h/db"


@pytest.mark.unit
def test_command_lists_results_db_without_a_flag():
    inspector = ConnectionInspector(
        _connections_with_results_db(
            _settings(connections={"db_prd6": "mssql+mssql-python://Server=s"})
        )
    )

    with _patch_inspector(inspector):
        result = CliRunner().invoke(monmon.cli, ["--dev", "connections"], obj={})

    assert result.exit_code == 0
    assert "results_db" in result.output
    assert "sqlite:///results.db" in result.output
    assert "2 connection(s)" in result.output
    assert "built-in" not in result.output


@pytest.mark.unit
def test_command_json_output():
    inspector = ConnectionInspector(_connections_with_results_db(_settings()))

    with _patch_inspector(inspector):
        result = CliRunner().invoke(
            monmon.cli, ["--dev", "connections", "--json"], obj={}
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {"name": "results_db", "kind": "db", "connection": "sqlite:///results.db"}
    ]


@pytest.mark.unit
def test_db_factory_can_create_the_results_db_engine():
    from locallib.db_connection_factory import DbConnectionFactory

    factory = DbConnectionFactory(
        connections=_connections_with_results_db(_settings()),
        passwords={},
    )

    engine = factory.create("results_db")
    assert engine.url.database == "results.db"


def _patch_inspector(inspector):
    from unittest.mock import patch

    return patch("monmon.get_connection_inspector", return_value=inspector)
