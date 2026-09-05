"""Strip secrets from logs and persisted job snapshots."""

from __future__ import annotations

import re

SECRET_LINE_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|github_token|token|pat|secret|password)\s*[:=]\s*\S+"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}")


def redact_text(text: str) -> str:
    if not text:
        return text
    out = SECRET_LINE_RE.sub(lambda m: re.split(r"[:=]", m.group(0), maxsplit=1)[0] + "=***", text)
    return BEARER_RE.sub("Bearer ***", out)


def redact_lines(lines: list[str]) -> list[str]:
    return [redact_text(line) for line in lines]


def looks_like_secret_key(key: str) -> bool:
    lowered = (key or "").lower()
    return any(part in lowered for part in ("token", "key", "secret", "password", "authorization", "pat"))
