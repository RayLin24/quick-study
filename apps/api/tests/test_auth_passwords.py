import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from app.auth.passwords import (
    ARGON2ID_HASHER,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    WeakPassword,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)

STRONG_PASSWORD = "correct horse battery staple"


def test_hasher_uses_argon2id_with_parameters_at_or_above_the_documented_floor() -> None:
    assert ARGON2ID_HASHER.type is Type.ID
    assert ARGON2ID_HASHER.time_cost >= 3
    assert ARGON2ID_HASHER.memory_cost >= 64 * 1024
    assert ARGON2ID_HASHER.parallelism >= 1
    assert ARGON2ID_HASHER.hash_len >= 32
    assert ARGON2ID_HASHER.salt_len >= 16


def test_hash_password_emits_an_argon2id_encoded_hash() -> None:
    assert hash_password(STRONG_PASSWORD).startswith("$argon2id$")


def test_hash_password_salts_every_hash_independently() -> None:
    assert hash_password(STRONG_PASSWORD) != hash_password(STRONG_PASSWORD)


def test_verify_password_accepts_the_original_secret() -> None:
    assert verify_password(hash_password(STRONG_PASSWORD), STRONG_PASSWORD) is True


def test_verify_password_rejects_a_wrong_secret() -> None:
    assert verify_password(hash_password(STRONG_PASSWORD), "wrong password entirely") is False


@pytest.mark.parametrize(
    "stored",
    ["", "not-a-hash", "$argon2id$v=19$m=65536,t=3,p=4$short", "$2b$12$abcdefghijklmnopqrstuv"],
)
def test_verify_password_reports_a_corrupt_stored_hash_as_a_failure(stored: str) -> None:
    assert verify_password(stored, STRONG_PASSWORD) is False


def test_verify_password_rejects_an_oversized_candidate_without_hashing_it() -> None:
    stored = hash_password(STRONG_PASSWORD)

    assert verify_password(stored, "a" * (MAX_PASSWORD_LENGTH + 1)) is False


@pytest.mark.parametrize("password", ["", "   ", "short", "a" * (MIN_PASSWORD_LENGTH - 1)])
def test_validate_password_strength_rejects_secrets_below_the_minimum_length(
    password: str,
) -> None:
    with pytest.raises(WeakPassword):
        validate_password_strength(password)


def test_validate_password_strength_rejects_secrets_above_the_denial_of_service_cap() -> None:
    with pytest.raises(WeakPassword):
        validate_password_strength("a" * (MAX_PASSWORD_LENGTH + 1))


def test_validate_password_strength_measures_the_cap_in_utf8_bytes() -> None:
    within_cap = "é" * (MAX_PASSWORD_LENGTH // 2)
    beyond_cap = "é" * MAX_PASSWORD_LENGTH

    validate_password_strength(within_cap)
    with pytest.raises(WeakPassword):
        validate_password_strength(beyond_cap)


def test_hash_password_refuses_to_persist_a_weak_secret() -> None:
    with pytest.raises(WeakPassword):
        hash_password("short")


def test_needs_rehash_is_false_for_a_freshly_created_hash() -> None:
    assert needs_rehash(hash_password(STRONG_PASSWORD)) is False


def test_needs_rehash_flags_hashes_created_with_weaker_parameters() -> None:
    legacy = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(STRONG_PASSWORD)

    assert needs_rehash(legacy) is True


def test_needs_rehash_flags_an_unreadable_hash_so_it_gets_replaced() -> None:
    assert needs_rehash("not-a-hash") is True
