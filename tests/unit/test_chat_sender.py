import json

import pytest

from locallib.chat_sender import (
    SLACK_POST_MESSAGE_URL,
    LoggingChatSender,
    SlackSender,
    SlackSettings,
    TeamsSender,
    TeamsSettings,
)
from locallib.http_client import HttpResponse

"""
Test: slack and teams delivery, without touching the network
"""


class FakeHttpClient:
    """Records posts and replays canned responses."""

    def __init__(self, status_code: int = 200, text: str = '{"ok": true}'):
        self.status_code = status_code
        self.text = text
        self.posts: list[tuple[str, dict, dict | None]] = []

    def post_json(self, url, payload, headers=None):
        self.posts.append((url, payload, headers))
        return HttpResponse(url=url, status_code=self.status_code, text=self.text)


@pytest.mark.unit
def test_logging_chat_sender_records_instead_of_sending():
    sender = LoggingChatSender("slack")

    sender.send(target="#alerts", subject="subject", body="body")

    assert len(sender.sent) == 1
    assert sender.sent[0].channel == "slack"
    assert sender.sent[0].target == "#alerts"


@pytest.mark.unit
def test_slack_posts_to_the_web_api_with_a_token():
    http = FakeHttpClient()
    sender = SlackSender(SlackSettings(token="xoxb-123"), http_client=http)

    sender.send(target="#alerts", subject="down", body="host0 is down")

    url, payload, headers = http.posts[0]
    assert url == SLACK_POST_MESSAGE_URL
    assert payload["channel"] == "#alerts"
    assert "host0 is down" in payload["text"]
    assert headers["Authorization"] == "Bearer xoxb-123"


@pytest.mark.unit
def test_slack_falls_back_to_a_webhook():
    http = FakeHttpClient()
    sender = SlackSender(
        SlackSettings(webhook_url="https://hooks.slack.test/default"), http_client=http
    )

    sender.send(target="@shane", subject="down", body="host0 is down")

    assert http.posts[0][0] == "https://hooks.slack.test/default"


@pytest.mark.unit
def test_slack_prefers_a_per_target_webhook():
    http = FakeHttpClient()
    sender = SlackSender(
        SlackSettings(
            webhook_url="https://hooks.slack.test/default",
            webhooks={"#alerts": "https://hooks.slack.test/alerts"},
        ),
        http_client=http,
    )

    sender.send(target="#alerts", subject="down", body="body")

    assert http.posts[0][0] == "https://hooks.slack.test/alerts"


@pytest.mark.unit
def test_slack_raises_when_the_api_rejects_the_message():
    http = FakeHttpClient(text=json.dumps({"ok": False, "error": "channel_not_found"}))
    sender = SlackSender(SlackSettings(token="xoxb-123"), http_client=http)

    with pytest.raises(RuntimeError, match="channel_not_found"):
        sender.send(target="#nope", subject="down", body="body")


@pytest.mark.unit
def test_slack_raises_on_an_http_error():
    http = FakeHttpClient(status_code=500, text="boom")
    sender = SlackSender(
        SlackSettings(webhook_url="https://hooks.slack.test/default"), http_client=http
    )

    with pytest.raises(RuntimeError, match="500"):
        sender.send(target="#alerts", subject="down", body="body")


@pytest.mark.unit
def test_slack_raises_when_no_webhook_matches():
    sender = SlackSender(SlackSettings(webhooks={"#other": "u"}), FakeHttpClient())

    with pytest.raises(ValueError):
        sender.send(target="#alerts", subject="down", body="body")


@pytest.mark.unit
def test_teams_posts_a_message_card_to_the_target_webhook():
    http = FakeHttpClient()
    sender = TeamsSender(
        TeamsSettings(webhooks={"#alerts": "https://outlook.test/alerts"}),
        http_client=http,
    )

    sender.send(target="#alerts", subject="down", body="host0 is down")

    url, payload, _ = http.posts[0]
    assert url == "https://outlook.test/alerts"
    assert payload["title"] == "down"
    assert payload["text"] == "host0 is down"


@pytest.mark.unit
def test_teams_raises_when_no_webhook_is_configured():
    sender = TeamsSender(TeamsSettings(), http_client=FakeHttpClient())

    with pytest.raises(ValueError):
        sender.send(target="#alerts", subject="down", body="body")


@pytest.mark.unit
@pytest.mark.parametrize(
    "settings,configured",
    [
        (SlackSettings(), False),
        (SlackSettings(token="x"), True),
        (TeamsSettings(), False),
        (TeamsSettings(webhook_url="u"), True),
    ],
)
def test_is_configured_reports_whether_credentials_exist(settings, configured):
    assert settings.is_configured is configured
