"""Thin Python SDK: list / ask / generate (feature 40)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class QuickStudy:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.getenv("QUICK_STUDY_TOKEN")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{exc.code} {detail}") from exc
        return json.loads(raw) if raw else {}

    def list_tutorials(self) -> list[dict]:
        return self._request("GET", "/v1/tutorials").get("items") or []

    def ask(self, tutorial: str, question: str) -> dict:
        return self._request("POST", f"/v1/tutorials/{tutorial}/ask", {"question": question})

    def generate(self, **payload) -> dict:
        return self._request("POST", "/v1/jobs", payload)
