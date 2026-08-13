from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.auth.accounts import (
    AdminAlreadyInitialised,
    EmailAlreadyRegistered,
    authenticate_user,
    bootstrap_admin,
    change_password,
    create_user,
    needs_bootstrap,
)
from app.auth.passwords import WeakPassword, verify_password
from app.db.models import BOOTSTRAP_SLOT, User
from app.db.models.enums import UserRole

ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "first administrator secret"
NOW = datetime(2026, 6, 7, 8, 9, 10, tzinfo=UTC)


def test_a_fresh_deployment_reports_that_it_needs_a_first_administrator(db: Session) -> None:
    assert needs_bootstrap(db) is True


def test_bootstrap_creates_an_active_administrator_with_a_hashed_password(db: Session) -> None:
    admin = bootstrap_admin(db, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, now=NOW)

    assert admin.role is UserRole.ADMIN
    assert admin.is_active is True
    assert admin.bootstrap_slot == BOOTSTRAP_SLOT
    assert admin.password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in admin.password_hash
    assert verify_password(admin.password_hash, ADMIN_PASSWORD) is True


def test_bootstrap_normalises_the_administrator_email(db: Session) -> None:
    admin = bootstrap_admin(db, email="  Admin@Example.TEST ", password=ADMIN_PASSWORD, now=NOW)

    assert admin.email == ADMIN_EMAIL


def test_a_bootstrapped_deployment_no_longer_needs_one(db: Session) -> None:
    bootstrap_admin(db, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, now=NOW)
    db.commit()

    assert needs_bootstrap(db) is False


def test_the_bootstrap_endpoint_cannot_be_used_twice(db: Session) -> None:
    bootstrap_admin(db, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, now=NOW)
    db.commit()

    with pytest.raises(AdminAlreadyInitialised):
        bootstrap_admin(db, email="second@example.test", password=ADMIN_PASSWORD, now=NOW)

    assert db.scalar(sa.select(sa.func.count()).select_from(User)) == 1


def test_bootstrap_refuses_a_weak_first_password(db: Session) -> None:
    with pytest.raises(WeakPassword):
        bootstrap_admin(db, email=ADMIN_EMAIL, password="admin", now=NOW)

    assert needs_bootstrap(db) is True


def test_an_administrator_can_add_a_member_account(db: Session) -> None:
    member = create_user(
        db,
        email="member@example.test",
        password="another long enough secret",
        display_name="Member",
    )

    assert member.role is UserRole.MEMBER
    assert member.bootstrap_slot is None
    assert verify_password(member.password_hash, "another long enough secret") is True


def test_created_accounts_get_a_display_name_even_without_one(db: Session) -> None:
    member = create_user(db, email="Nina.Doe@example.test", password="another long secret")

    assert member.display_name == "Nina.Doe"


def test_authenticating_with_the_right_password_returns_the_account(db: Session) -> None:
    create_user(db, email="member@example.test", password="another long enough secret")
    db.commit()

    user = authenticate_user(
        db, email="Member@example.test", password="another long enough secret", now=NOW
    )

    assert user is not None
    assert user.email == "member@example.test"
    assert user.last_login_at == NOW


def test_authenticating_with_the_wrong_password_fails_without_touching_the_account(
    db: Session,
) -> None:
    create_user(db, email="member@example.test", password="another long enough secret")
    db.commit()

    assert authenticate_user(db, email="member@example.test", password="wrong", now=NOW) is None
    assert db.scalar(sa.select(User.last_login_at)) is None


def test_an_unknown_email_fails_the_same_way_as_a_wrong_password(db: Session) -> None:
    assert authenticate_user(db, email="nobody@example.test", password="whatever") is None


def test_a_deactivated_account_cannot_authenticate(db: Session) -> None:
    member = create_user(db, email="member@example.test", password="another long enough secret")
    member.is_active = False
    db.commit()

    assert (
        authenticate_user(db, email="member@example.test", password="another long enough secret")
        is None
    )


def test_a_successful_login_upgrades_a_hash_created_with_weaker_parameters(
    db: Session,
) -> None:
    from argon2 import PasswordHasher

    member = create_user(db, email="member@example.test", password="another long enough secret")
    member.password_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
        "another long enough secret"
    )
    legacy_hash = member.password_hash
    db.commit()

    user = authenticate_user(
        db, email="member@example.test", password="another long enough secret"
    )

    assert user is not None
    assert user.password_hash != legacy_hash
    assert verify_password(user.password_hash, "another long enough secret") is True


def test_changing_a_password_replaces_the_hash_and_records_when(db: Session) -> None:
    member = create_user(db, email="member@example.test", password="another long enough secret")
    original = member.password_hash

    change_password(db, member, new_password="a completely different secret", now=NOW)

    assert member.password_hash != original
    assert member.password_changed_at == NOW
    assert verify_password(member.password_hash, "a completely different secret") is True


def test_changing_a_password_refuses_a_weak_replacement(db: Session) -> None:
    member = create_user(db, email="member@example.test", password="another long enough secret")
    original = member.password_hash

    with pytest.raises(WeakPassword):
        change_password(db, member, new_password="short")

    assert member.password_hash == original


def test_duplicate_accounts_are_refused_case_insensitively(db: Session) -> None:
    """The unique index is what enforces it; the caller sees a domain error, not SQL."""
    create_user(db, email="member@example.test", password="another long enough secret")
    db.commit()

    with pytest.raises(EmailAlreadyRegistered):
        create_user(db, email="MEMBER@EXAMPLE.TEST", password="another long enough secret")

    assert db.scalar(sa.select(sa.func.count()).select_from(User)) == 1


def test_a_refused_duplicate_leaves_the_session_usable(db: Session) -> None:
    """A failed insert must not poison the transaction the request is still using."""
    create_user(db, email="member@example.test", password="another long enough secret")
    db.commit()

    with pytest.raises(EmailAlreadyRegistered):
        create_user(db, email="member@example.test", password="another long enough secret")

    other = create_user(db, email="other@example.test", password="another long enough secret")
    db.commit()

    assert other.id is not None
    assert db.scalar(sa.select(sa.func.count()).select_from(User)) == 2
