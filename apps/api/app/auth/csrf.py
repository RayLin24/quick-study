from __future__ import annotations

from typing import Final

from app.auth.tokens import tokens_match

CSRF_HEADER_NAME: Final = "X-CSRF-Token"
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def method_requires_csrf(method: str) -> bool:
    return method.upper() not in SAFE_METHODS


def verify_csrf_token(session_csrf_fingerprint: str, presented: str | None) -> bool:
    """Validate a synchroniser token bound to the caller's session row.

    The secret is handed out in a response body rather than a readable cookie, so a
    cross-site caller cannot learn it even when the browser attaches the session cookie.
    """
    return tokens_match(session_csrf_fingerprint, presented)
