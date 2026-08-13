from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db.base import TABLE_OPTIONS, Base, IdMixin, TimestampMixin
from app.db.models._columns import enum_column, id_fk, project_fk
from app.db.models.enums import (
    ApprovalDecision,
    ApprovalSubject,
    ChapterStatus,
    CitationKind,
    ClaimKind,
    ClaimStatus,
    OutlineStatus,
)
from app.db.types import ID_LENGTH, Confidence


class Outline(IdMixin, TimestampMixin, Base):
    """A proposed table of contents awaiting, holding or superseded by human approval.

    Editing an approved outline creates the next ``version`` for the same run rather than
    mutating the approved row, so an approval always points at exactly what was approved.
    """

    __tablename__ = "outlines"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "version", name="uq_outlines_run_version"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk()
    run_id: Mapped[str] = id_fk("runs.id", ondelete="CASCADE", index=False)
    version: Mapped[int] = mapped_column(sa.Integer, default=1)
    status: Mapped[OutlineStatus] = enum_column(OutlineStatus, OutlineStatus.DRAFT)
    title: Mapped[str] = mapped_column(sa.String(512), default="")
    summary: Mapped[str] = mapped_column(sa.Text, default="")
    structure: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[str | None] = id_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )


class Chapter(IdMixin, TimestampMixin, Base):
    """One chapter of the tutorial. Markdown lives in content-addressed storage.

    ``revision`` and ``locked_at`` are what stop a partial regeneration from overwriting
    text a reviewer already accepted.
    """

    __tablename__ = "chapters"
    __table_args__ = (
        sa.UniqueConstraint("outline_id", "ordinal", name="uq_chapters_outline_ordinal"),
        sa.UniqueConstraint("outline_id", "slug", name="uq_chapters_outline_slug"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk()
    outline_id: Mapped[str] = id_fk("outlines.id", ondelete="CASCADE", index=False)
    ordinal: Mapped[int] = mapped_column(sa.Integer)
    slug: Mapped[str] = mapped_column(sa.String(160))
    title: Mapped[str] = mapped_column(sa.String(512))
    status: Mapped[ChapterStatus] = enum_column(ChapterStatus, ChapterStatus.PENDING)
    summary: Mapped[str] = mapped_column(sa.Text, default="")
    word_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    revision: Mapped[int] = mapped_column(sa.Integer, default=1)
    content_artifact_id: Mapped[str | None] = id_fk(
        "artifacts.id", ondelete="SET NULL", nullable=True, index=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    locked_by: Mapped[str | None] = id_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )


class Claim(IdMixin, TimestampMixin, Base):
    """An atomic statement a chapter makes, which the quality gate has to back up."""

    __tablename__ = "claims"
    __table_args__ = (
        sa.Index("ix_claims_project_status", "project_id", "status"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    run_id: Mapped[str | None] = id_fk("runs.id", ondelete="SET NULL", nullable=True, index=False)
    chapter_id: Mapped[str | None] = id_fk("chapters.id", ondelete="CASCADE", nullable=True)
    kind: Mapped[ClaimKind] = enum_column(ClaimKind, ClaimKind.FACT)
    status: Mapped[ClaimStatus] = enum_column(ClaimStatus, ClaimStatus.UNVERIFIED)
    statement: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[Decimal] = mapped_column(Confidence, default=Decimal("1.000"))


class Citation(IdMixin, Base):
    """Where a claim came from, pinned to an immutable snapshot.

    ``locator`` is the citation as a reader sees it: a page URL plus chunk anchor, or
    ``repo@commit/path#Lx-Ly``.
    """

    __tablename__ = "citations"
    __table_args__ = (
        sa.Index("ix_citations_project_id", "project_id"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    claim_id: Mapped[str | None] = id_fk("claims.id", ondelete="CASCADE", nullable=True)
    chapter_id: Mapped[str | None] = id_fk("chapters.id", ondelete="CASCADE", nullable=True)
    kind: Mapped[CitationKind] = enum_column(CitationKind)
    snapshot_id: Mapped[str] = id_fk("snapshots.id", ondelete="CASCADE")
    document_id: Mapped[str | None] = id_fk(
        "documents.id", ondelete="SET NULL", nullable=True, index=False
    )
    chunk_id: Mapped[str | None] = id_fk(
        "chunks.id", ondelete="SET NULL", nullable=True, index=False
    )
    symbol_id: Mapped[str | None] = id_fk(
        "symbols.id", ondelete="SET NULL", nullable=True, index=False
    )
    locator: Mapped[str] = mapped_column(sa.String(1024))
    quote: Mapped[str] = mapped_column(sa.Text, default="")
    start_line: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Approval(IdMixin, TimestampMixin, Base):
    """A human decision on a source scope, an outline, a chapter or a publication.

    ``subject_id`` is deliberately a bare id rather than a foreign key: approvals outlive
    the rows they judge, and the audit trail must survive a superseded outline.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        sa.Index("ix_approvals_project_subject", "project_id", "subject_type", "subject_id"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    run_id: Mapped[str | None] = id_fk("runs.id", ondelete="SET NULL", nullable=True, index=False)
    subject_type: Mapped[ApprovalSubject] = enum_column(ApprovalSubject)
    subject_id: Mapped[str] = mapped_column(sa.String(ID_LENGTH))
    decision: Mapped[ApprovalDecision] = enum_column(ApprovalDecision, ApprovalDecision.PENDING)
    requested_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_by: Mapped[str | None] = id_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    note: Mapped[str] = mapped_column(sa.Text, default="")
