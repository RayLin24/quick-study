import re

import pytest

from app.auth.csrf import (
    CSRF_HEADER_NAME,
    method_requires_csrf,
    verify_csrf_token,
)
from app.auth.tokens import (
    TOKEN_ENTROPY_BYTES,
    fingerprint_token,
    generate_token,
    issue_token,
    tokens_match,
)

URL_SAFE = re.compile(r"\A[A-Za-z0-9_-]+\Z")
SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")


def test_generated_tokens_are_url_safe_and_carry_at_least_256_bits() -> None:
    token = generate_token()

    assert TOKEN_ENTROPY_BYTES >= 32
    assert URL_SAFE.match(token)
    assert len(token) >= 40


def test_generated_tokens_are_unique_across_calls() -> None:
    assert len({generate_token() for _ in range(64)}) == 64


def test_fingerprint_is_a_stable_sha256_hex_digest() -> None:
    token = generate_token()

    fingerprint = fingerprint_token(token)

    assert SHA256_HEX.match(fingerprint)
    assert fingerprint == fingerprint_token(token)
    assert fingerprint != token


def test_issue_token_returns_the_secret_next_to_the_value_that_gets_persisted() -> None:
    issued = issue_token()

    assert issued.fingerprint == fingerprint_token(issued.secret)
    assert issued.secret not in issued.fingerprint


def test_tokens_match_only_when_the_presented_secret_hashes_to_the_fingerprint() -> None:
    issued = issue_token()

    assert tokens_match(issued.fingerprint, issued.secret) is True
    assert tokens_match(issued.fingerprint, generate_token()) is False


@pytest.mark.parametrize("presented", [None, "", "   ", "not-the-token"])
def test_tokens_match_rejects_missing_or_blank_secrets(presented: str | None) -> None:
    issued = issue_token()

    assert tokens_match(issued.fingerprint, presented) is False


def test_tokens_match_rejects_a_blank_fingerprint_so_unbound_rows_cannot_authenticate() -> None:
    assert tokens_match("", generate_token()) is False


def test_csrf_header_name_is_the_documented_one() -> None:
    assert CSRF_HEADER_NAME == "X-CSRF-Token"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE", "get", "head"])
def test_read_only_methods_do_not_require_a_csrf_token(method: str) -> None:
    assert method_requires_csrf(method) is False


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "delete"])
def test_state_changing_methods_require_a_csrf_token(method: str) -> None:
    assert method_requires_csrf(method) is True


def test_verify_csrf_token_matches_the_session_bound_fingerprint() -> None:
    issued = issue_token()

    assert verify_csrf_token(issued.fingerprint, issued.secret) is True
    assert verify_csrf_token(issued.fingerprint, generate_token()) is False
    assert verify_csrf_token(issued.fingerprint, None) is False
