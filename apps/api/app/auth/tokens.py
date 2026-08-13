from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Final

TOKEN_ENTROPY_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A bearer secret plus the only form of it that is allowed to reach the database."""

    secret: str
    fingerprint: str


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def fingerprint_token(token: str) -> str:
    """Return the SHA-256 fingerprint stored instead of the secret itself.

    A database dump therefore cannot be replayed as a session cookie or CSRF token.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token() -> IssuedToken:
    secret = generate_token()
    return IssuedToken(secret=secret, fingerprint=fingerprint_token(secret))


def tokens_match(fingerprint: str, presented: str | None) -> bool:
    if not fingerprint or not presented or not presented.strip():
        return False
    return hmac.compare_digest(fingerprint, fingerprint_token(presented))
