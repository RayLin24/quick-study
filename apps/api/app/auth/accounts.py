from __future__ import annotations

from datetime import datetime
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.auth.sessions import revoke_user_sessions
from app.clock import utcnow
from app.db.models import BOOTSTRAP_SLOT, User
from app.db.models.enums import UserRole


class AccountError(Exception):
    """Base class for account management failures."""


class AdminAlreadyInitialised(AccountError):
    """Raised when the first-administrator flow is used on a deployment that has one."""


class EmailAlreadyRegistered(AccountError):
    """Raised when an address is already taken, whatever case it was typed in."""


def needs_bootstrap(session: Session) -> bool:
    """Return whether this deployment still has to create its first administrator."""
    return session.scalar(sa.select(sa.func.count()).select_from(User)) == 0


def bootstrap_admin(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
    now: datetime | None = None,
) -> User:
    """Create the first administrator, exactly once per deployment.

    The check below gives a clear error; the unique ``bootstrap_slot`` is what makes it
    correct, because two simultaneous requests would both pass a read-only check.
    """
    validate_password_strength(password)
    if not needs_bootstrap(session):
        raise AdminAlreadyInitialised("this deployment already has an administrator")

    admin = _build_user(
        email=email,
        password=password,
        display_name=display_name,
        role=UserRole.ADMIN,
        now=now,
    )
    admin.bootstrap_slot = BOOTSTRAP_SLOT
    savepoint = session.begin_nested()
    try:
        session.add(admin)
        session.flush()
    except IntegrityError as error:
        savepoint.rollback()
        raise AdminAlreadyInitialised("this deployment already has an administrator") from error
    savepoint.commit()
    return admin


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    role: UserRole = UserRole.MEMBER,
    display_name: str | None = None,
    now: datetime | None = None,
) -> User:
    """Create an account, letting the unique index decide whether the address is free.

    The insert runs in a savepoint so a duplicate leaves the caller's transaction usable
    instead of failing everything that shares it, and the caller sees a domain error rather
    than the driver's.
    """
    user = _build_user(
        email=email, password=password, display_name=display_name, role=role, now=now
    )
    savepoint = session.begin_nested()
    try:
        session.add(user)
        session.flush()
    except IntegrityError as error:
        savepoint.rollback()
        raise EmailAlreadyRegistered(f"{_normalise_email(email)} already has an account") from error
    savepoint.commit()
    return user


def authenticate_user(
    session: Session,
    *,
    email: str,
    password: str,
    now: datetime | None = None,
) -> User | None:
    """Verify a local account's password, upgrading its hash when the cost has moved on.

    An unknown address still pays for one verification so response time does not reveal
    which addresses exist.
    """
    user = session.scalars(
        sa.select(User).where(User.email == _normalise_email(email))
    ).one_or_none()
    if user is None:
        verify_password(_unusable_hash(), password)
        return None
    if not verify_password(user.password_hash, password) or not user.is_active:
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = now or utcnow()
    session.flush()
    return user


def change_password(
    session: Session,
    user: User,
    *,
    new_password: str,
    revoke_sessions: bool = True,
    now: datetime | None = None,
) -> None:
    """Replace a password and, by default, end every session that used the old one."""
    validate_password_strength(new_password)
    moment = now or utcnow()
    user.password_hash = hash_password(new_password)
    user.password_changed_at = moment
    session.flush()
    if revoke_sessions:
        revoke_user_sessions(session, user.id, now=moment)


def _build_user(
    *,
    email: str,
    password: str,
    display_name: str | None,
    role: UserRole,
    now: datetime | None,
) -> User:
    normalised = _normalise_email(email)
    moment = now or utcnow()
    return User(
        email=normalised,
        # Derived from what the operator typed: the address is case-folded for uniqueness,
        # but a person's name is not the place to lose their capitalisation.
        display_name=display_name or email.strip().split("@")[0],
        password_hash=hash_password(password),
        role=role,
        password_changed_at=moment,
    )


def _normalise_email(email: str) -> str:
    return email.strip().lower()


@lru_cache(maxsize=1)
def _unusable_hash() -> str:
    """A hash of an unguessable secret, used only to spend time on unknown accounts."""
    from secrets import token_urlsafe

    return hash_password(token_urlsafe(32))
