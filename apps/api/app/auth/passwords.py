from __future__ import annotations

from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerificationError
from argon2.low_level import Type

MIN_PASSWORD_LENGTH: Final = 12
MAX_PASSWORD_LENGTH: Final = 1024

ARGON2ID_HASHER: Final = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


class WeakPassword(ValueError):
    """Raised when a candidate password may not be stored."""


def validate_password_strength(password: str) -> None:
    """Enforce the only two rules a self-hosted deployment can enforce honestly.

    A length floor keeps offline cracking expensive, and a byte cap stops an
    unauthenticated caller from turning Argon2's memory cost into a denial of service.
    """
    stripped = password.strip()
    if len(stripped) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"passwords need at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        raise WeakPassword(f"passwords may not exceed {MAX_PASSWORD_LENGTH} bytes")


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return ARGON2ID_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return whether ``password`` matches ``password_hash``, never raising on bad input."""
    if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return ARGON2ID_HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError, Argon2Error, TypeError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return whether a stored hash should be replaced on the next successful login."""
    try:
        return ARGON2ID_HASHER.check_needs_rehash(password_hash)
    except (InvalidHashError, Argon2Error, TypeError, ValueError):
        return True
