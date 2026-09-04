"""Single source for CLI/Web default tutorial language."""

from __future__ import annotations

import os

DEFAULT_LANGUAGE = (os.getenv("DEFAULT_LANGUAGE") or "Chinese").strip() or "Chinese"


def normalize_language(value: str | None) -> str:
    text = (value or "").strip()
    return text or DEFAULT_LANGUAGE
