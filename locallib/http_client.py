"""Minimal http client wrapper used by the web monitor tests."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass

import requests
import urllib3


@dataclass
class HttpResponse:
    url: str
    status_code: int
    text: str
    elapsed_seconds: float = 0.0

    def json(self):
        return json.loads(self.text)


class HttpClient:
    def __init__(self, timeout_seconds: int = 30, verify_ssl: bool = True):
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl

    def get(
        self,
        url: str,
        headers: dict | None = None,
        verify_ssl: bool | None = None,
        ca_bundle: str | None = None,
    ) -> HttpResponse:
        """Fetch `url` with GET, following redirects."""
        return self.request(
            "GET", url, headers=headers, verify_ssl=verify_ssl, ca_bundle=ca_bundle
        )

    def request(
        self,
        method: str,
        url: str,
        headers: dict | None = None,
        body: str | bytes | None = None,
        verify_ssl: bool | None = None,
        ca_bundle: str | None = None,
        allow_redirects: bool = True,
    ) -> HttpResponse:
        """Send a `method` request to `url`.

        `method` is passed through untouched.  `verify_ssl` overrides the
        client default for this request only - `None` means "use the client
        default".  `ca_bundle` is a path to a PEM file to verify against
        instead of the system store, and is ignored when verification is off.
        """
        verify = self._verify(verify_ssl, ca_bundle)

        with warnings.catch_warnings():
            if verify is False:
                warnings.simplefilter(
                    "ignore", urllib3.exceptions.InsecureRequestWarning
                )
            response = requests.request(
                method,
                url,
                timeout=self.timeout_seconds,
                allow_redirects=allow_redirects,
                verify=verify,
                headers=headers,
                data=body,
            )

        return HttpResponse(
            url=url,
            status_code=response.status_code,
            text=response.text,
            elapsed_seconds=response.elapsed.total_seconds(),
        )

    def post_json(
        self, url: str, payload: dict, headers: dict | None = None
    ) -> HttpResponse:
        response = requests.post(
            url,
            json=payload,
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
            headers=headers,
        )

        return HttpResponse(
            url=url,
            status_code=response.status_code,
            text=response.text,
            elapsed_seconds=response.elapsed.total_seconds(),
        )

    def _verify(self, verify_ssl: bool | None, ca_bundle: str | None) -> bool | str:
        verify = self.verify_ssl if verify_ssl is None else verify_ssl
        if verify and ca_bundle:
            return ca_bundle

        return verify
