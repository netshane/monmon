import warnings
from datetime import timedelta

import pytest
import urllib3

from locallib.http_client import HttpClient


class FakeResponse:
    status_code = 200
    text = "{}"
    elapsed = timedelta(seconds=0.1)


@pytest.fixture
def requests_get(monkeypatch):
    calls = []

    def fake_get(method, url, **kwargs):
        calls.append(kwargs)
        if kwargs.get("verify") is False:
            warnings.warn("insecure", urllib3.exceptions.InsecureRequestWarning)
        return FakeResponse()

    monkeypatch.setattr("locallib.http_client.requests.request", fake_get)
    return calls


@pytest.mark.unit
def test_get_uses_client_default_when_unset(requests_get):
    HttpClient(verify_ssl=False).get("https://example.com")
    HttpClient(verify_ssl=True).get("https://example.com")

    assert [c["verify"] for c in requests_get] == [False, True]


@pytest.mark.unit
def test_get_override_wins_over_client_default(requests_get):
    HttpClient(verify_ssl=True).get("https://example.com", verify_ssl=False)
    HttpClient(verify_ssl=False).get("https://example.com", verify_ssl=True)

    assert [c["verify"] for c in requests_get] == [False, True]


@pytest.mark.unit
def test_get_ca_bundle_replaces_verify_when_verifying(requests_get):
    client = HttpClient(verify_ssl=True)
    client.get("https://example.com", ca_bundle="/etc/ca.pem")
    client.get("https://example.com", verify_ssl=False, ca_bundle="/etc/ca.pem")

    assert [c["verify"] for c in requests_get] == ["/etc/ca.pem", False]


@pytest.mark.unit
def test_get_suppresses_insecure_warning_only_when_disabled(requests_get):
    client = HttpClient(verify_ssl=True)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.get("https://example.com", verify_ssl=False)

    assert requests_get[-1]["verify"] is False


@pytest.fixture
def requests_request(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("locallib.http_client.requests.request", fake_request)
    return calls


@pytest.mark.unit
def test_request_forwards_everything(requests_request):
    HttpClient(timeout_seconds=7, verify_ssl=True).request(
        "PURGE",
        "https://example.com/x",
        headers={"A": "b"},
        body='{"a": 1}',
        verify_ssl=False,
        allow_redirects=False,
    )

    call = requests_request[0]
    assert call["method"] == "PURGE"
    assert call["url"] == "https://example.com/x"
    assert call["headers"] == {"A": "b"}
    assert call["data"] == '{"a": 1}'
    assert call["verify"] is False
    assert call["allow_redirects"] is False
    assert call["timeout"] == 7


@pytest.mark.unit
def test_get_is_a_get_request_following_redirects(requests_request):
    response = HttpClient().get("https://example.com", headers={"A": "b"})

    call = requests_request[0]
    assert call["method"] == "GET"
    assert call["allow_redirects"] is True
    assert call["data"] is None
    assert response.status_code == 200
