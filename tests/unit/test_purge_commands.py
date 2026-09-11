from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import monmon

"""
Test: the purge and purge-monitor cli commands
"""


@pytest.fixture()
def service():
    service = MagicMock()
    service.repository.purge_runs_before.return_value = 3
    service.repository.purge_monitor.return_value = 5

    with patch("monmon.get_monitor_service", return_value=service) as factory:
        yield service, factory


@pytest.mark.unit
def test_purge_defaults_to_ninety_days_for_every_monitor(service):
    service, _ = service

    result = CliRunner().invoke(monmon.cli, ["--dev", "purge"], obj={})

    assert result.exit_code == 0
    _, kwargs = service.repository.purge_runs_before.call_args
    assert kwargs["monitor_name"] is None
    assert "Deleted 3 run(s)" in result.output


@pytest.mark.unit
def test_purge_accepts_a_monitor_filter(service):
    service, _ = service

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "purge", "--days", "30", "--monitor", "xyz"], obj={}
    )

    assert result.exit_code == 0
    _, kwargs = service.repository.purge_runs_before.call_args
    assert kwargs["monitor_name"] == "xyz"
    assert "for monitor 'xyz'" in result.output


@pytest.mark.unit
def test_purge_monitor_deletes_everything_for_one_monitor(service):
    service, _ = service

    result = CliRunner().invoke(monmon.cli, ["--dev", "purge-monitor", "xyz"], obj={})

    assert result.exit_code == 0
    service.repository.purge_monitor.assert_called_once_with("xyz")
    assert "Deleted 5 run(s)" in result.output
    assert "xyz" in result.output


@pytest.mark.unit
def test_purge_also_prunes_the_email_send_log(service):
    service, _ = service
    service.repository.purge_email_send_log_before.return_value = 7

    result = CliRunner().invoke(monmon.cli, ["--dev", "purge"], obj={})

    assert result.exit_code == 0
    service.repository.purge_email_send_log_before.assert_called_once()
    assert "Deleted 7 email send log row(s)" in result.output


@pytest.mark.unit
def test_purging_one_monitor_leaves_the_email_send_log_alone(service):
    service, _ = service

    result = CliRunner().invoke(
        monmon.cli, ["--dev", "purge", "--monitor", "xyz"], obj={}
    )

    assert result.exit_code == 0
    service.repository.purge_email_send_log_before.assert_not_called()
    assert "email send log" not in result.output
