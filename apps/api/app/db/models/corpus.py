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
    CodeLanguage,
    DocumentKind,
    EdgeKind,
    SnapshotStatus,
    SourceKind,
    SymbolKind,
)
from app.db.types import Confidence, LongText, Sha256


class Source(IdMixin, TimestampMixin, Base):
    """A public documentation site or public GitHub repository a project may draw on.

    ``locator_fingerprint`` is the SHA-256 of the canonical locator: MySQL cannot index a
    1 KB string inside a composite unique key, and the digest is stable across
    normalisation of the same input.
    """

    __tablename__ = "sources"
    __table_args__ = (
        sa.UniqueConstraint(
            "project_id", "locator_fingerprint", name="uq_sources_project_locator"
        ),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    kind: Mapped[SourceKind] = enum_column(SourceKind)
    locator: Mapped[str] = mapped_column(sa.String(1024))
    locator_fingerprint: Mapped[str] = mapped_column(Sha256)
    display_name: Mapped[str] = mapped_column(sa.String(255), default="")
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Snapshot(IdMixin, TimestampMixin, Base):
    """One immutable capture of a source: a crawl at a point in time or a pinned commit.

    Every citation resolves against a snapshot, so a snapshot is never rewritten;
    re-fetching a source creates a new row with a new fingerprint.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "fingerprint", name="uq_snapshots_source_fingerprint"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk()
    source_id: Mapped[str] = id_fk("sources.id", ondelete="CASCADE", index=False)
    status: Mapped[SnapshotStatus] = enum_column(SnapshotStatus, SnapshotStatus.PENDING)
    fingerprint: Mapped[str] = mapped_column(Sha256)
    commit_sha: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(nullable=True)
    document_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    byte_size: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    manifest_artifact_id: Mapped[str | None] = id_fk(
        "artifacts.id", ondelete="SET NULL", nullable=True, index=False
    )
    failure_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class Document(IdMixin, TimestampMixin, Base):
    """A page or repository file inside a snapshot.

    ``body_text`` is the normalised plain text kept in MySQL purely so FULLTEXT can index
    it. The authoritative bytes stay in content-addressed storage and are referenced by
    ``raw_artifact_id`` and ``normalized_artifact_id``.
    """

    __tablename__ = "documents"
    __table_args__ = (
        sa.UniqueConstraint("snapshot_id", "uri_fingerprint", name="uq_documents_snapshot_uri"),
        sa.Index("ft_documents_title", "title", mysql_prefix="FULLTEXT"),
        sa.Index("ft_documents_body_text", "body_text", mysql_prefix="FULLTEXT"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk()
    snapshot_id: Mapped[str] = id_fk("snapshots.id", ondelete="CASCADE", index=False)
    source_id: Mapped[str] = id_fk("sources.id", ondelete="CASCADE")
    kind: Mapped[DocumentKind] = enum_column(DocumentKind)
    uri: Mapped[str] = mapped_column(sa.String(1024))
    uri_fingerprint: Mapped[str] = mapped_column(Sha256)
    path: Mapped[str] = mapped_column(sa.String(768), default="")
    title: Mapped[str] = mapped_column(sa.String(512), default="")
    code_language: Mapped[CodeLanguage | None] = enum_column(CodeLanguage, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    body_text: Mapped[str] = mapped_column(LongText, default="")
    body_sha256: Mapped[str | None] = mapped_column(Sha256, nullable=True)
    byte_size: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    raw_artifact_id: Mapped[str | None] = id_fk(
        "artifacts.id", ondelete="SET NULL", nullable=True, index=False
    )
    normalized_artifact_id: Mapped[str | None] = id_fk(
        "artifacts.id", ondelete="SET NULL", nullable=True, index=False
    )


class Chunk(IdMixin, Base):
    """A citable slice of a document; ``anchor`` is what a reference points at."""

    __tablename__ = "chunks"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        sa.Index("ft_chunks_text", "text", mysql_prefix="FULLTEXT"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk()
    document_id: Mapped[str] = id_fk("documents.id", ondelete="CASCADE", index=False)
    ordinal: Mapped[int] = mapped_column(sa.Integer)
    anchor: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    heading_path: Mapped[str] = mapped_column(sa.String(768), default="")
    text: Mapped[str] = mapped_column(LongText, default="")
    char_start: Mapped[int] = mapped_column(sa.Integer, default=0)
    char_end: Mapped[int] = mapped_column(sa.Integer, default=0)
    token_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    sha256: Mapped[str] = mapped_column(Sha256)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Symbol(IdMixin, Base):
    """A definition extracted from analysed source code."""

    __tablename__ = "symbols"
    __table_args__ = (
        sa.Index("ix_symbols_project_qualified_name", "project_id", "qualified_name"),
        sa.Index(
            "ft_symbols_identifier",
            "name",
            "qualified_name",
            "signature",
            "docstring",
            mysql_prefix="FULLTEXT",
        ),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    document_id: Mapped[str] = id_fk("documents.id", ondelete="CASCADE")
    kind: Mapped[SymbolKind] = enum_column(SymbolKind)
    language: Mapped[CodeLanguage] = enum_column(CodeLanguage)
    name: Mapped[str] = mapped_column(sa.String(255))
    qualified_name: Mapped[str] = mapped_column(sa.String(512))
    signature: Mapped[str] = mapped_column(sa.String(1024), default="")
    docstring: Mapped[str] = mapped_column(LongText, default="")
    start_line: Mapped[int] = mapped_column(sa.Integer, default=0)
    end_line: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Edge(IdMixin, Base):
    """A relationship between symbols, with the confidence the analyser assigned.

    Unresolved targets keep their textual name in ``to_name`` instead of inventing a
    symbol row, so a low-confidence call edge never fabricates a definition.
    """

    __tablename__ = "edges"
    __table_args__ = (
        sa.Index("ix_edges_project_kind", "project_id", "kind"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    kind: Mapped[EdgeKind] = enum_column(EdgeKind)
    from_symbol_id: Mapped[str] = id_fk("symbols.id", ondelete="CASCADE")
    to_symbol_id: Mapped[str | None] = id_fk("symbols.id", ondelete="SET NULL", nullable=True)
    to_name: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    document_id: Mapped[str | None] = id_fk(
        "documents.id", ondelete="SET NULL", nullable=True, index=False
    )
    line: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Confidence, default=Decimal("1.000"))
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
