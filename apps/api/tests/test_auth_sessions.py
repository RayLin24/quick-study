from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from conftest import make_user
from sqlalchemy.orm import Session

from app.auth.sessions import (
    DEFAULT_ACTIVITY_REFRESH,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_SESSION_TTL,
    authenticate_session,
    create_session,
    revoke_session,
    revoke_user_sessions,
)
from app.auth.tokens import fingerprint_token
from app.db.models import UserSession

NOW = datetime(2026, 7, 8, 9, 10, 11, tzinfo=UTC)


def test_a_new_session_hands_out_a_token_and_a_csrf_secret(db: Session) -> None:
    user = make_user(db)

    issued = create_session(db, user, now=NOW)

    assert issued.token
    assert issued.csrf_token
    assert issued.token != issued.csrf_token
    assert issued.record.user_id == user.id
    assert issued.record.expires_at == NOW + DEFAULT_SESSION_TTL


def test_only_fingerprints_of_the_secrets_reach_the_database(db: Session) -> None:
    """A database dump must not be replayable as a login or as a CSRF token."""
    user = make_user(db)

    issued = create_session(db, user, now=NOW)
    db.commit()

    stored = db.execute(sa.text("SELECT * FROM sessions")).mappings().one()
    serialised = " ".join(str(value) for value in stored.values())
    assert issued.token not in serialised
    assert issued.csrf_token not in serialised
    assert stored["token_fingerprint"] == fingerprint_token(issued.token)
    assert stored["csrf_fingerprint"] == fingerprint_token(issued.csrf_token)


def test_a_session_records_the_client_it_was_created_from(db: Session) -> None:
    user = make_user(db)

    issued = create_session(db, user, user_agent="Firefox/140", client_ip="10.0.0.7", now=NOW)

    assert issued.record.user_agent == "Firefox/140"
    assert issued.record.client_ip == "10.0.0.7"


def test_an_over_long_user_agent_is_truncated_rather_than_rejected(db: Session) -> None:
    user = make_user(db)

    issued = create_session(db, user, user_agent="x" * 500, now=NOW)

    assert len(issued.record.user_agent or "") == 255


def test_authenticating_a_valid_token_returns_the_session_and_its_user(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()

    authenticated = authenticate_session(db, issued.token, now=NOW + timedelta(minutes=5))

    assert authenticated is not None
    assert authenticated.user.id == user.id
    assert authenticated.record.id == issued.record.id


def test_authenticating_refreshes_the_idle_clock(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()
    later = NOW + timedelta(minutes=30)

    authenticate_session(db, issued.token, now=later)

    assert issued.record.last_seen_at == later


def test_the_refreshed_idle_clock_outlives_a_read_only_request(db: Session) -> None:
    """Read paths never commit, so a refresh that is only flushed is thrown away."""
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()
    later = NOW + timedelta(minutes=30)

    authenticate_session(db, issued.token, now=later)
    db.rollback()
    db.expire_all()

    reloaded = db.get(UserSession, issued.record.id)
    assert reloaded is not None
    assert reloaded.last_seen_at == later


def test_a_user_who_keeps_working_is_never_logged_out_by_the_idle_timeout(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()

    moment = NOW
    for _ in range(5):
        moment += DEFAULT_IDLE_TIMEOUT - timedelta(minutes=1)
        assert authenticate_session(db, issued.token, now=moment) is not None
        db.rollback()


def test_a_burst_of_requests_refreshes_the_clock_at_most_once(db: Session) -> None:
    """The write is throttled: every read paying for one would be a needless cost."""
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()

    authenticate_session(db, issued.token, now=NOW + DEFAULT_ACTIVITY_REFRESH / 2)
    db.rollback()
    db.expire_all()

    reloaded = db.get(UserSession, issued.record.id)
    assert reloaded is not None
    assert reloaded.last_seen_at == NOW


def test_an_unknown_token_authenticates_nobody(db: Session) -> None:
    make_user(db)

    assert authenticate_session(db, "not-a-real-token", now=NOW) is None


def test_a_token_that_only_matches_a_prefix_authenticates_nobody(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()

    assert authenticate_session(db, issued.token[:-1], now=NOW) is None


def test_a_session_past_its_absolute_expiry_authenticates_nobody(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()

    expired_at = NOW + DEFAULT_SESSION_TTL + timedelta(seconds=1)
    assert authenticate_session(db, issued.token, now=expired_at) is None


def test_a_session_left_idle_too_long_authenticates_nobody(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    db.commit()

    idle_at = NOW + DEFAULT_IDLE_TIMEOUT + timedelta(seconds=1)
    assert authenticate_session(db, issued.token, now=idle_at) is None


def test_a_revoked_session_authenticates_nobody(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    revoke_session(db, issued.record, now=NOW)
    db.commit()

    assert issued.record.revoked_at == NOW
    assert authenticate_session(db, issued.token, now=NOW + timedelta(minutes=1)) is None


def test_a_session_belonging_to_a_deactivated_user_authenticates_nobody(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    user.is_active = False
    db.commit()

    assert authenticate_session(db, issued.token, now=NOW + timedelta(minutes=1)) is None


def test_revoking_every_session_of_a_user_logs_them_out_everywhere(db: Session) -> None:
    user = make_user(db)
    other = make_user(db)
    first = create_session(db, user, now=NOW)
    second = create_session(db, user, now=NOW)
    untouched = create_session(db, other, now=NOW)
    db.commit()

    revoked = revoke_user_sessions(db, user.id, now=NOW)
    db.commit()

    assert revoked == 2
    assert authenticate_session(db, first.token, now=NOW) is None
    assert authenticate_session(db, second.token, now=NOW) is None
    assert authenticate_session(db, untouched.token, now=NOW) is not None


def test_revoking_an_already_revoked_session_is_harmless(db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=NOW)
    revoke_session(db, issued.record, now=NOW)

    revoke_session(db, issued.record, now=NOW + timedelta(minutes=1))

    assert issued.record.revoked_at == NOW


def test_every_session_gets_its_own_secrets(db: Session) -> None:
    user = make_user(db)

    tokens = {create_session(db, user, now=NOW).token for _ in range(8)}

    assert len(tokens) == 8
    assert db.scalar(sa.select(sa.func.count()).select_from(UserSession)) == 8
