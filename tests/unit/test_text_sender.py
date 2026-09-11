import subprocess

import pytest
from twilio.base.exceptions import TwilioRestException

from locallib.text_sender import (
    MAX_TEXT_LENGTH,
    ImsgSender,
    LoggingTextSender,
    TwilioSender,
    TwilioSettings,
)

"""
Test: imsg / twilio text delivery, without touching the network or shelling out
"""


class FakeTwilioMessages:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.created: list[dict] = []

    def create(self, to, from_, body):
        if self.error:
            raise self.error
        self.created.append({"to": to, "from_": from_, "body": body})


class FakeTwilioClient:
    def __init__(self, error: Exception | None = None):
        self.messages = FakeTwilioMessages(error=error)


@pytest.mark.unit
def test_logging_text_sender_records_instead_of_sending():
    sender = LoggingTextSender(provider="imsg")

    sender.send(target="+14155551212", subject="down", body="host0 is down")

    assert len(sender.sent) == 1
    assert sender.sent[0].target == "+14155551212"
    assert sender.sent[0].provider == "imsg"
    assert sender.is_live is False


@pytest.mark.unit
def test_logging_text_sender_truncates_and_warns():
    sender = LoggingTextSender()
    body = "x" * (MAX_TEXT_LENGTH + 50)

    sender.send(target="+14155551212", subject="down", body=body)

    assert len(sender.sent[0].body) == MAX_TEXT_LENGTH


@pytest.mark.unit
def test_imsg_sender_shells_out_to_the_cli(monkeypatch):
    monkeypatch.setattr("locallib.text_sender.platform.system", lambda: "Darwin")

    calls = []

    def fake_run(command, capture_output, text):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("locallib.text_sender.subprocess.run", fake_run)

    ImsgSender().send(target="+14155551212", subject="down", body="host0 is down")

    assert calls[0] == [
        "imsg",
        "send",
        "--to",
        "+14155551212",
        "--text",
        "host0 is down",
        "--service",
        "imessage",
    ]


@pytest.mark.unit
def test_imsg_sender_fails_when_not_on_a_mac(monkeypatch):
    monkeypatch.setattr("locallib.text_sender.platform.system", lambda: "Linux")

    with pytest.raises(RuntimeError, match="macOS"):
        ImsgSender().send(target="+14155551212", subject="down", body="body")


@pytest.mark.unit
def test_imsg_sender_fails_when_not_installed(monkeypatch):
    monkeypatch.setattr("locallib.text_sender.platform.system", lambda: "Darwin")

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("locallib.text_sender.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="not installed"):
        ImsgSender().send(target="+14155551212", subject="down", body="body")


@pytest.mark.unit
def test_imsg_sender_fails_on_a_nonzero_exit(monkeypatch):
    monkeypatch.setattr("locallib.text_sender.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "locallib.text_sender.subprocess.run",
        lambda command, capture_output, text: subprocess.CompletedProcess(
            command, returncode=1, stdout="", stderr="boom"
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        ImsgSender().send(target="+14155551212", subject="down", body="body")


@pytest.mark.unit
def test_imsg_sender_truncates_and_warns(monkeypatch):
    monkeypatch.setattr("locallib.text_sender.platform.system", lambda: "Darwin")

    calls = []

    def fake_run(command, capture_output, text):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("locallib.text_sender.subprocess.run", fake_run)

    ImsgSender().send(
        target="+14155551212", subject="down", body="x" * (MAX_TEXT_LENGTH + 20)
    )

    sent_body = calls[0][calls[0].index("--text") + 1]
    assert len(sent_body) == MAX_TEXT_LENGTH


@pytest.mark.unit
def test_twilio_sender_creates_a_message():
    fake_client = FakeTwilioClient()
    sender = TwilioSender(
        TwilioSettings(
            account_sid="SIDxxx", auth_token="tokenxxx", from_number="+15550000000"
        ),
        client=fake_client,
    )

    sender.send(target="+14155551212", subject="down", body="host0 is down")

    assert fake_client.messages.created == [
        {"to": "+14155551212", "from_": "+15550000000", "body": "host0 is down"}
    ]


@pytest.mark.unit
def test_twilio_sender_truncates_and_warns():
    fake_client = FakeTwilioClient()
    sender = TwilioSender(
        TwilioSettings(
            account_sid="SIDxxx", auth_token="tokenxxx", from_number="+15550000000"
        ),
        client=fake_client,
    )

    sender.send(
        target="+14155551212", subject="down", body="x" * (MAX_TEXT_LENGTH + 20)
    )

    assert len(fake_client.messages.created[0]["body"]) == MAX_TEXT_LENGTH


@pytest.mark.unit
def test_twilio_sender_raises_on_api_error():
    error = TwilioRestException(status=400, uri="/Messages", msg="invalid number")
    fake_client = FakeTwilioClient(error=error)
    sender = TwilioSender(
        TwilioSettings(
            account_sid="SIDxxx", auth_token="tokenxxx", from_number="+15550000000"
        ),
        client=fake_client,
    )

    with pytest.raises(RuntimeError, match="invalid number"):
        sender.send(target="+14155551212", subject="down", body="body")


@pytest.mark.unit
@pytest.mark.parametrize(
    "settings,configured",
    [
        (TwilioSettings(), False),
        (TwilioSettings(account_sid="s"), False),
        (
            TwilioSettings(account_sid="s", auth_token="t", from_number="+1"),
            True,
        ),
    ],
)
def test_twilio_is_configured_reports_whether_credentials_exist(settings, configured):
    assert settings.is_configured is configured
