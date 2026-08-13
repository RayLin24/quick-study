"""Freezing a crawl into something a tutorial can cite.

A snapshot is immutable. Its identity is the fingerprint of what was captured, so
re-running a step that was delivered twice converges on the same row instead of producing
a second copy, and a site that has actually changed produces a new snapshot rather than
editing history out from under existing citations.

Payloads — the original HTML, the normalised Markdown, the manifest — go to
content-addressed storage. MySQL keeps paths, digests and provenance, plus the plain text
that FULLTEXT has to index in order to be an index at all.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Snapshot, Source
from app.db.models.enums import ArtifactKind, DocumentKind, SnapshotStatus
from app.ingestion.citations import format_web_citation
from app.ingestion.web.chunking import chunk_markdown
from app.ingestion.web.crawler import CrawledPage, CrawlResult
from app.ingestion.web.dedupe import DuplicateIndex, DuplicateKind, DuplicateVerdict
from app.ingestion.web.extract import ExtractedDocument, ExtractionOutcome, extract_document
from app.storage.artifacts import write_artifact
from app.storage.content_store import ContentAddressedStore

MANIFEST_SCHEMA_VERSION: Final = "1.0.0"

#: MySQL's ``documents.title`` is 512 characters wide.
MAX_TITLE_LENGTH: Final = 512


@dataclass(frozen=True, slots=True)
class WebSnapshotOutcome:
    """What one capture produced, and whether it was already there."""

    snapshot: Snapshot
    documents: tuple[Document, ...]
    chunk_count: int
    duplicates: tuple[DuplicateVerdict, ...]
    citations: tuple[str, ...]
    manifest: dict[str, Any]
    reused: bool = False


def persist_web_snapshot(
    session: Session,
    store: ContentAddressedStore,
    *,
    source: Source,
    crawl: CrawlResult,
    run_id: str | None = None,
    step_id: str | None = None,
) -> WebSnapshotOutcome:
    """Record ``crawl`` as an immutable snapshot of ``source``."""
    prepared = [_prepare(page) for page in crawl.pages]
    fingerprint = _fingerprint(crawl, prepared)

    existing = session.scalars(
        sa.select(Snapshot).where(
            Snapshot.source_id == source.id, Snapshot.fingerprint == fingerprint
        )
    ).one_or_none()
    if existing is not None:
        return _reuse(session, existing)

    snapshot = Snapshot(
        project_id=source.project_id,
        source_id=source.id,
        status=SnapshotStatus.FETCHING,
        fingerprint=fingerprint,
        captured_at=crawl.finished_at,
    )
    session.add(snapshot)
    session.flush()

    write = _ArtifactWriter(session, store, source.project_id, run_id, step_id)
    documents: list[Document] = []
    duplicates: list[DuplicateVerdict] = []
    citations: list[str] = []
    manifest_pages: list[dict[str, Any]] = []
    index = DuplicateIndex()
    chunk_count = 0
    byte_size = 0

    for page, extracted in prepared:
        entry = _manifest_entry(page, extracted)
        manifest_pages.append(entry)
        if extracted.outcome is ExtractionOutcome.EMPTY or not extracted.markdown:
            continue

        verdict = index.add(str(page.url), extracted.text)
        if verdict.is_duplicate:
            duplicates.append(verdict)
            entry["duplicate_of"] = verdict.original_key
            entry["duplicate_kind"] = verdict.kind.value
            continue

        raw = write(page.content, ArtifactKind.RAW_HTML, page.media_type, _provenance(page))
        normalized = write(
            extracted.markdown.encode("utf-8"),
            ArtifactKind.NORMALIZED_CORPUS,
            "text/markdown",
            _provenance(page),
        )
        entry["raw_sha256"] = raw.sha256
        entry["normalized_sha256"] = normalized.sha256

        document = Document(
            project_id=source.project_id,
            snapshot_id=snapshot.id,
            source_id=source.id,
            kind=DocumentKind.WEB_PAGE,
            uri=str(page.url),
            uri_fingerprint=_digest(str(page.url)),
            path=page.url.path,
            title=(extracted.title or page.url.path)[:MAX_TITLE_LENGTH],
            mime_type=page.media_type,
            body_text=extracted.text,
            body_sha256=_digest(extracted.text),
            byte_size=len(page.content),
            raw_artifact_id=raw.id,
            normalized_artifact_id=normalized.id,
        )
        session.add(document)
        session.flush()
        documents.append(document)
        byte_size += len(page.content)

        chunks = chunk_markdown(extracted.markdown)
        chunk_count += len(chunks)
        for chunk in chunks:
            session.add(
                Chunk(
                    project_id=source.project_id,
                    document_id=document.id,
                    ordinal=chunk.ordinal,
                    anchor=chunk.anchor,
                    heading_path=chunk.heading_path,
                    text=chunk.text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    token_count=chunk.token_count,
                    sha256=chunk.sha256,
                )
            )
        if chunks:
            citations.append(
                format_web_citation(str(page.url), page.fetched_at, chunks[0].anchor)
            )
    session.flush()

    manifest = _manifest(crawl, manifest_pages, fingerprint)
    manifest_artifact = write(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        ArtifactKind.SNAPSHOT_MANIFEST,
        "application/json",
        {"snapshot_fingerprint": fingerprint},
    )

    snapshot.status = SnapshotStatus.READY
    snapshot.document_count = len(documents)
    snapshot.byte_size = byte_size
    snapshot.manifest_artifact_id = manifest_artifact.id
    session.flush()

    return WebSnapshotOutcome(
        snapshot=snapshot,
        documents=tuple(documents),
        chunk_count=chunk_count,
        duplicates=tuple(duplicates),
        citations=tuple(citations),
        manifest=manifest,
    )


class _ArtifactWriter:
    """Writes a payload and records it, carrying the same provenance every time."""

    def __init__(
        self,
        session: Session,
        store: ContentAddressedStore,
        project_id: str,
        run_id: str | None,
        step_id: str | None,
    ) -> None:
        self._session = session
        self._store = store
        self._project_id = project_id
        self._run_id = run_id
        self._step_id = step_id

    def __call__(
        self,
        payload: bytes,
        kind: ArtifactKind,
        media_type: str,
        provenance: dict[str, Any],
    ):
        return write_artifact(
            self._session,
            self._store,
            payload,
            project_id=self._project_id,
            kind=kind,
            media_type=media_type,
            provenance=provenance,
            run_id=self._run_id,
            step_id=self._step_id,
        )


def _prepare(page: CrawledPage) -> tuple[CrawledPage, ExtractedDocument]:
    return page, extract_document(page.content, page.url, charset=page.charset)


def _fingerprint(
    crawl: CrawlResult,
    prepared: Sequence[tuple[CrawledPage, ExtractedDocument]],
) -> str:
    """Identify a capture by its content, so an identical re-crawl is the same snapshot."""
    digest = hashlib.sha256()
    digest.update(f"{crawl.seed}\n".encode())
    for page, _ in sorted(prepared, key=lambda item: str(item[0].url)):
        digest.update(f"{page.url}\t{hashlib.sha256(page.content).hexdigest()}\n".encode())
    return digest.hexdigest()


def _reuse(session: Session, snapshot: Snapshot) -> WebSnapshotOutcome:
    documents = tuple(
        session.scalars(
            sa.select(Document)
            .where(Document.snapshot_id == snapshot.id)
            .order_by(Document.uri)
        ).all()
    )
    chunk_count = session.scalar(
        sa.select(sa.func.count())
        .select_from(Chunk)
        .where(Chunk.document_id.in_([document.id for document in documents]))
    )
    return WebSnapshotOutcome(
        snapshot=snapshot,
        documents=documents,
        chunk_count=int(chunk_count or 0),
        duplicates=(),
        citations=(),
        manifest={},
        reused=True,
    )


def _provenance(page: CrawledPage) -> dict[str, Any]:
    return {
        "url": str(page.url),
        "requested_url": str(page.requested_url),
        "fetched_at": _instant(page),
        "status_code": page.status_code,
        "addresses": list(page.addresses),
        "redirect_chain": [str(hop) for hop in page.redirect_chain],
        "discovered_via": page.discovered_via,
    }


def _manifest_entry(page: CrawledPage, extracted: ExtractedDocument) -> dict[str, Any]:
    return {
        **_provenance(page),
        "media_type": page.media_type,
        "depth": page.depth,
        "byte_size": len(page.content),
        "outcome": extracted.outcome.value,
        "title": extracted.title,
        "raw_sha256": None,
        "normalized_sha256": None,
        "duplicate_of": None,
        "duplicate_kind": DuplicateKind.UNIQUE.value,
    }


def _manifest(
    crawl: CrawlResult,
    pages: list[dict[str, Any]],
    fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "seed": str(crawl.seed),
        "scope": {
            "scheme": crawl.scope.scheme,
            "host": crawl.scope.host,
            "port": crawl.scope.port,
            "path_prefix": crawl.scope.path_prefix,
            "include_subdomains": crawl.scope.include_subdomains,
        },
        "started_at": _iso(crawl.started_at),
        "finished_at": _iso(crawl.finished_at),
        "stopped_because": crawl.stopped_because,
        "total_bytes": crawl.total_bytes,
        "pages": pages,
        "skipped": [
            {"url": str(entry.url), "reason": entry.reason.value, "detail": entry.detail}
            for entry in crawl.skipped
        ],
    }


def _instant(page: CrawledPage) -> str:
    return _iso(page.fetched_at)


def _iso(moment: Any) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
