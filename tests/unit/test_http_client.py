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

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        if kwargs.get("verify") is False:
            warnings.warn("insecure", urllib3.exceptions.InsecureRequestWarning)
        return FakeResponse()

    monkeypatch.setattr("locallib.http_client.requests.get", fake_get)
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
