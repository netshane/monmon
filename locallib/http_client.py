"""Minimal http client wrapper used by the web monitor tests."""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests


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

    def get(self, url: str, headers: dict | None = None) -> HttpResponse:
        response = requests.get(
            url,
            timeout=self.timeout_seconds,
            allow_redirects=True,
            verify=self.verify_ssl,
            headers=headers,
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
