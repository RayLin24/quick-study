from __future__ import annotations

from datetime import datetime
from typing import Final

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.clock import utcnow
from app.db.base import TABLE_OPTIONS, Base, IdMixin, TimestampMixin
from app.db.models._columns import enum_column, id_fk
from app.db.models.enums import UserRole
from app.db.types import Sha256

#: The single value ``users.bootstrap_slot`` may hold. A unique constraint on a nullable
#: column allows many NULLs but only one claimant, which makes "the deployment already has
#: its first administrator" a database guarantee instead of a read-then-write race.
BOOTSTRAP_SLOT: Final = "admin"


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = TABLE_OPTIONS

    email: Mapped[str] = mapped_column(sa.String(320), unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(120))
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    role: Mapped[UserRole] = enum_column(UserRole, UserRole.MEMBER)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    bootstrap_slot: Mapped[str | None] = mapped_column(sa.String(16), nullable=True, unique=True)
    password_changed_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)

    @validates("email")
    def _normalise_email(self, _key: str, value: str) -> str:
        """Fold case so the unique constraint cannot be sidestepped by capitalisation."""
        return value.strip().lower()


class UserSession(IdMixin, Base):
    """A server-side session record; the cookie value itself is never stored.

    ``token_fingerprint`` and ``csrf_fingerprint`` hold SHA-256 digests, so a database
    dump cannot be replayed as a login or used to forge a state-changing request.
    """

    __tablename__ = "sessions"
    __table_args__ = TABLE_OPTIONS

    user_id: Mapped[str] = id_fk("users.id", ondelete="CASCADE")
    token_fingerprint: Mapped[str] = mapped_column(Sha256, unique=True)
    csrf_fingerprint: Mapped[str] = mapped_column(Sha256)
    user_agent: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(sa.String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
