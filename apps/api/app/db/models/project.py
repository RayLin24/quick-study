from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import TABLE_OPTIONS, Base, IdMixin, TimestampMixin
from app.db.models._columns import enum_column, id_fk, project_fk
from app.db.models.enums import (
    LengthPreset,
    ProjectRole,
    ProjectStatus,
    ReaderLevel,
)


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = TABLE_OPTIONS

    owner_id: Mapped[str] = id_fk("users.id", ondelete="RESTRICT")
    slug: Mapped[str] = mapped_column(sa.String(96), unique=True)
    name: Mapped[str] = mapped_column(sa.String(160))
    description: Mapped[str] = mapped_column(sa.Text, default="")
    output_language: Mapped[str] = mapped_column(sa.String(16), default="zh")
    reader_level: Mapped[ReaderLevel] = enum_column(ReaderLevel, ReaderLevel.BEGINNER)
    length_preset: Mapped[LengthPreset] = enum_column(LengthPreset, LengthPreset.STANDARD)
    status: Mapped[ProjectStatus] = enum_column(ProjectStatus, ProjectStatus.DRAFT)


class ProjectMember(IdMixin, TimestampMixin, Base):
    """Grants one user one role on one project; absence of a row means no access."""

    __tablename__ = "project_members"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    user_id: Mapped[str] = id_fk("users.id", ondelete="CASCADE")
    role: Mapped[ProjectRole] = enum_column(ProjectRole, ProjectRole.VIEWER)
