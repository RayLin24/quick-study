from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import sqlalchemy as sa
from fastapi import Response
from sqlalchemy.orm import Session

from app.auth.tokens import fingerprint_token, issue_token
from app.clock import utcnow
from app.db.models import User, UserSession
from app.settings import Settings, get_settings

#: How long a session may live at all, however active the user is.
DEFAULT_SESSION_TTL: Final = timedelta(hours=12)

#: How long a session survives without being used. Shorter than the absolute lifetime so
#: an unattended browser stops being a valid credential.
DEFAULT_IDLE_TIMEOUT: Final = timedelta(hours=2)

#: How stale ``last_seen_at`` may become before a request pays for a write. Short enough to
#: be invisible against the idle timeout, long enough that a burst of requests writes once.
DEFAULT_ACTIVITY_REFRESH: Final = timedelta(minutes=1)

#: ``Lax`` still sends the cookie on top-level navigation, which keeps ordinary links
#: working, while withholding it from cross-site form posts and subresource requests.
SESSION_COOKIE_SAMESITE: Final = "Lax"
SESSION_COOKIE_PATH: Final = "/"

_USER_AGENT_MAX_LENGTH: Final = 255


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A stored session plus the two secrets that are only ever returned once."""

    record: UserSession
    token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    record: UserSession
    user: User


def create_session(
    session: Session,
    user: User,
    *,
    user_agent: str | None = None,
    client_ip: str | None = None,
    ttl: timedelta = DEFAULT_SESSION_TTL,
    now: datetime | None = None,
) -> IssuedSession:
    """Open a session for ``user``, storing only fingerprints of its two secrets."""
    moment = now or utcnow()
    token = issue_token()
    csrf = issue_token()
    record = UserSession(
        user_id=user.id,
        token_fingerprint=token.fingerprint,
        csrf_fingerprint=csrf.fingerprint,
        user_agent=user_agent[:_USER_AGENT_MAX_LENGTH] if user_agent else None,
        client_ip=client_ip,
        created_at=moment,
        last_seen_at=moment,
        expires_at=moment + ttl,
    )
    session.add(record)
    session.flush()
    return IssuedSession(record=record, token=token.secret, csrf_token=csrf.secret)


def authenticate_session(
    session: Session,
    token: str | None,
    *,
    idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
    refresh_after: timedelta = DEFAULT_ACTIVITY_REFRESH,
    now: datetime | None = None,
) -> AuthenticatedSession | None:
    """Resolve a session cookie to its session and user, or nothing.

    Every rejection reason returns the same answer so a caller cannot tell an expired
    session from a forged one.

    Success slides the idle window forward and commits that by itself. It has to: most
    authenticated requests only read, and their transaction is rolled back, so a refresh
    that was merely flushed would be discarded and an active user would still be logged out
    the moment the absolute idle timeout elapsed. The commit is the caller's session, so
    authenticate before the request writes anything of its own -- which is where a request
    establishes who is calling anyway. The write is throttled by ``refresh_after`` so a
    burst of reads pays for at most one.
    """
    if not token or not token.strip():
        return None
    moment = now or utcnow()
    record = session.scalars(
        sa.select(UserSession).where(UserSession.token_fingerprint == fingerprint_token(token))
    ).one_or_none()
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at <= moment or moment - record.last_seen_at > idle_timeout:
        return None
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    if moment - record.last_seen_at >= refresh_after:
        record.last_seen_at = moment
        session.commit()
    return AuthenticatedSession(record=record, user=user)


def revoke_session(session: Session, record: UserSession, *, now: datetime | None = None) -> None:
    """Invalidate one session, keeping the first revocation time if already revoked."""
    if record.revoked_at is None:
        record.revoked_at = now or utcnow()
        session.flush()


def revoke_user_sessions(
    session: Session,
    user_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Log a user out of every device; returns how many sessions were still live."""
    result = session.execute(
        sa.update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now or utcnow())
    )
    return result.rowcount


def set_session_cookie(
    response: Response,
    token: str,
    *,
    settings: Settings | None = None,
    ttl: timedelta = DEFAULT_SESSION_TTL,
) -> None:
    """Attach the session cookie.

    HttpOnly so no script can read it, SameSite=Lax so it is not attached to cross-site
    state-changing requests, and Secure unless the deployment explicitly opts out.
    """
    resolved = settings or get_settings()
    response.set_cookie(
        key=resolved.session_cookie_name,
        value=token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=resolved.session_cookie_secure,
        samesite=SESSION_COOKIE_SAMESITE,
        path=SESSION_COOKIE_PATH,
    )


def clear_session_cookie(response: Response, *, settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    response.delete_cookie(
        key=resolved.session_cookie_name,
        httponly=True,
        secure=resolved.session_cookie_secure,
        samesite=SESSION_COOKIE_SAMESITE,
        path=SESSION_COOKIE_PATH,
    )
