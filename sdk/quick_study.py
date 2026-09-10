"""Thin Python SDK: list / ask / generate + preview / export / wait / zip (#28)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class QuickStudy:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.getenv("QUICK_STUDY_TOKEN")

    def _headers(self, *, json_body: bool = True) -> dict:
        headers = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, body: dict | None = None, *, timeout: float = 60) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=self._headers(json_body=body is not None or method in {"POST", "PUT", "PATCH"}),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{exc.code} {detail}") from exc
        return json.loads(raw) if raw else {}

    def _bytes(self, path: str, *, timeout: float = 120) -> bytes:
        req = urllib.request.Request(self.base_url + path, headers=self._headers(json_body=False), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{exc.code} {detail}") from exc

    def list_tutorials(self) -> list[dict]:
        return self._request("GET", "/v1/tutorials").get("items") or []

    def ask(self, tutorial: str, question: str, *, include_source: bool = False) -> dict:
        return self._request(
            "POST",
            f"/v1/tutorials/{tutorial}/ask",
            {"question": question, "include_source": include_source},
        )

    def generate(self, **payload) -> dict:
        return self._request("POST", "/v1/jobs", payload)

    def preview(self, **payload) -> dict:
        return self._request("POST", "/v1/jobs/preview", payload)

    def export_zip(self, tutorial: str) -> bytes:
        return self._bytes(f"/v1/tutorials/{tutorial}/export.zip")

    def fetch_zip(self, tutorial: str) -> bytes:
        return self.export_zip(tutorial)

    def job_status(self, after: int | None = None) -> dict:
        path = "/v1/jobs/current"
        if after is not None:
            path += f"?after={int(after)}"
        return self._request("GET", path)

    def wait_for_job(self, *, timeout: float = 3600, poll: float = 1.0, sse: bool = True) -> dict:
        """Block until the current job leaves `running`. Prefer SSE; fall back to poll."""
        deadline = time.monotonic() + max(1.0, float(timeout))
        if sse:
            try:
                return self._wait_sse(deadline)
            except Exception:
                pass
        last = None
        while time.monotonic() < deadline:
            last = self.job_status()
            if (last.get("status") or "idle") != "running":
                return last
            time.sleep(max(0.05, float(poll)))
        raise TimeoutError("job still running")

    def _wait_sse(self, deadline: float) -> dict:
        req = urllib.request.Request(
            self.base_url + "/v1/jobs/current/events",
            headers={**self._headers(json_body=False), "Accept": "text/event-stream"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=max(5, deadline - time.monotonic())) as resp:
            buf = ""
            while time.monotonic() < deadline:
                chunk = resp.read(256)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.splitlines():
                        if not line.startswith("data:"):
                            continue
                        payload = json.loads(line[5:].strip() or "{}")
                        if payload.get("type") == "done":
                            return payload
        return self.job_status()
