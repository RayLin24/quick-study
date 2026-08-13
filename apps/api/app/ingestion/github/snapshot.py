"""Freezing a repository at one commit into citable documents.

The commit SHA is chosen once, before anything is listed, and every subsequent request and
every citation is addressed against it. A repository read twice a week apart is therefore
either byte-identical or visibly a different snapshot; there is no state in between.

Files are excluded as early as the evidence allows. Most are refused from the tree entry
alone and are never requested at all, which is what keeps a checked-in secret out of the
network path, not only out of the corpus. The rest — Git LFS pointers, files whose bytes
turn out to be binary — can only be judged after download and are dropped there.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Snapshot, Source
from app.db.models.enums import ArtifactKind, CodeLanguage, DocumentKind, SnapshotStatus
from app.ingestion.citations import format_repo_citation
from app.ingestion.github.client import GitHubClient
from app.ingestion.github.filters import ExclusionReason, FileFilter
from app.ingestion.github.refs import RepositoryRef
from app.ingestion.github.tree import RepositoryListing, TreeLimits, collect_files
from app.ingestion.web.chunking import chunk_markdown
from app.storage.artifacts import write_artifact
from app.storage.content_store import ContentAddressedStore

MANIFEST_SCHEMA_VERSION: Final = "1.0.0"

#: How many source lines one citable chunk covers.
CHUNK_LINES: Final = 60

MAX_TITLE_LENGTH: Final = 512

#: Extensions the deep analysers understand. Everything else is documentation or config.
LANGUAGE_BY_EXTENSION: Final[dict[str, CodeLanguage]] = {
    ".py": CodeLanguage.PYTHON,
    ".pyi": CodeLanguage.PYTHON,
    ".ts": CodeLanguage.TYPESCRIPT,
    ".tsx": CodeLanguage.TYPESCRIPT,
    ".mts": CodeLanguage.TYPESCRIPT,
    ".cts": CodeLanguage.TYPESCRIPT,
    ".js": CodeLanguage.JAVASCRIPT,
    ".jsx": CodeLanguage.JAVASCRIPT,
    ".mjs": CodeLanguage.JAVASCRIPT,
    ".cjs": CodeLanguage.JAVASCRIPT,
}

PROSE_EXTENSIONS: Final[frozenset[str]] = frozenset({".md", ".mdx", ".markdown", ".rst", ".txt"})


@dataclass(frozen=True, slots=True)
class ExcludedFile:
    """One file that was left out, and why."""

    path: str
    reason: ExclusionReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RepositorySnapshotOutcome:
    """What one capture produced."""

    snapshot: Snapshot
    listing: RepositoryListing
    documents: tuple[Document, ...] = ()
    excluded: tuple[ExcludedFile, ...] = ()
    citations: tuple[str, ...] = ()
    manifest: dict[str, Any] = None  # type: ignore[assignment]
    reused: bool = False


def capture_repository_snapshot(
    session: Session,
    store: ContentAddressedStore,
    *,
    source: Source,
    client: GitHubClient,
    ref: RepositoryRef,
    revision: str | None = None,
    file_filter: FileFilter | None = None,
    tree_limits: TreeLimits | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
) -> RepositorySnapshotOutcome:
    """Read ``ref`` at a pinned commit and record it as an immutable snapshot."""
    rules = file_filter or FileFilter()
    commit = client.resolve_commit(ref, revision)
    listing = collect_files(client, ref, commit, limits=tree_limits)
    fingerprint = _fingerprint(ref, commit, listing)

    existing = session.scalars(
        sa.select(Snapshot).where(
            Snapshot.source_id == source.id, Snapshot.fingerprint == fingerprint
        )
    ).one_or_none()
    if existing is not None:
        return _reuse(session, existing, listing)

    snapshot = Snapshot(
        project_id=source.project_id,
        source_id=source.id,
        status=SnapshotStatus.FETCHING,
        fingerprint=fingerprint,
        commit_sha=commit,
        captured_at=datetime.now(UTC),
    )
    session.add(snapshot)
    session.flush()

    documents: list[Document] = []
    excluded: list[ExcludedFile] = []
    citations: list[str] = []
    byte_size = 0

    for entry in listing.files:
        decision = rules.decide(entry)
        if not decision.included:
            excluded.append(ExcludedFile(entry.path, decision.reason, decision.detail))
            continue

        content = client.blob(ref, entry.sha)
        verdict = rules.inspect_content(entry.path, content)
        if not verdict.included:
            excluded.append(ExcludedFile(entry.path, verdict.reason, verdict.detail))
            continue

        text = _decode(content)
        if text is None:
            excluded.append(ExcludedFile(entry.path, ExclusionReason.BINARY, "not utf-8"))
            continue

        artifact = write_artifact(
            session,
            store,
            content,
            project_id=source.project_id,
            kind=ArtifactKind.RAW_REPO_FILE,
            media_type="text/plain",
            provenance=_provenance(ref, commit, entry.path, entry.sha),
            run_id=run_id,
            step_id=step_id,
        )
        document = Document(
            project_id=source.project_id,
            snapshot_id=snapshot.id,
            source_id=source.id,
            kind=DocumentKind.REPO_FILE,
            uri=_blob_url(ref, commit, entry.path),
            uri_fingerprint=_digest(_blob_url(ref, commit, entry.path)),
            path=entry.path,
            title=entry.path.rsplit("/", 1)[-1][:MAX_TITLE_LENGTH],
            code_language=language_of(entry.path),
            mime_type="text/plain",
            body_text=text,
            body_sha256=_digest(text),
            byte_size=len(content),
            raw_artifact_id=artifact.id,
        )
        session.add(document)
        session.flush()
        documents.append(document)
        byte_size += len(content)

        chunks = _chunk(entry.path, text)
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
        citations.extend(
            format_repo_citation(
                ref.full_name, commit, entry.path, chunk.start_line, chunk.end_line
            )
            for chunk in chunks
        )
    session.flush()

    manifest = _manifest(ref, commit, listing, documents, excluded, fingerprint)
    manifest_artifact = write_artifact(
        session,
        store,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        project_id=source.project_id,
        kind=ArtifactKind.SNAPSHOT_MANIFEST,
        media_type="application/json",
        provenance={"repository": ref.full_name, "commit": commit},
        run_id=run_id,
        step_id=step_id,
    )

    snapshot.status = SnapshotStatus.READY
    snapshot.document_count = len(documents)
    snapshot.byte_size = byte_size
    snapshot.manifest_artifact_id = manifest_artifact.id
    session.flush()

    return RepositorySnapshotOutcome(
        snapshot=snapshot,
        listing=listing,
        documents=tuple(documents),
        excluded=tuple(excluded),
        citations=tuple(citations),
        manifest=manifest,
    )


def language_of(path: str) -> CodeLanguage | None:
    """Return the analysable language of ``path``, or ``None`` when it is not code."""
    index = path.rfind(".")
    return LANGUAGE_BY_EXTENSION.get(path[index:].lower()) if index > 0 else None


@dataclass(frozen=True, slots=True)
class _RepoChunk:
    ordinal: int
    anchor: str
    heading_path: str
    text: str
    char_start: int
    char_end: int
    token_count: int
    sha256: str
    start_line: int
    end_line: int


def _chunk(path: str, text: str) -> tuple[_RepoChunk, ...]:
    """Chunk prose by structure and code by line window, so both cite cleanly."""
    extension = path[path.rfind(".") :].lower() if "." in path else ""
    if extension in PROSE_EXTENSIONS:
        return tuple(
            _RepoChunk(
                ordinal=chunk.ordinal,
                anchor=f"L{_line_of(text, chunk.char_start)}-"
                f"L{_line_of(text, max(chunk.char_end - 1, chunk.char_start))}",
                heading_path=chunk.heading_path or path,
                text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                token_count=chunk.token_count,
                sha256=chunk.sha256,
                start_line=_line_of(text, chunk.char_start),
                end_line=_line_of(text, max(chunk.char_end - 1, chunk.char_start)),
            )
            for chunk in chunk_markdown(text)
        )
    return _line_windows(path, text)


def _line_windows(path: str, text: str, *, window: int = CHUNK_LINES) -> tuple[_RepoChunk, ...]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return ()
    chunks: list[_RepoChunk] = []
    offset = 0
    for index in range(0, len(lines), window):
        block = lines[index : index + window]
        body = "".join(block)
        start_line = index + 1
        end_line = index + len(block)
        chunks.append(
            _RepoChunk(
                ordinal=len(chunks),
                anchor=f"L{start_line}-L{end_line}",
                heading_path=path,
                text=body.rstrip("\n"),
                char_start=offset,
                char_end=offset + len(body),
                token_count=len(body.split()),
                sha256=_digest(body),
                start_line=start_line,
                end_line=end_line,
            )
        )
        offset += len(body)
    return tuple(chunks)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def _decode(content: bytes) -> str | None:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _blob_url(ref: RepositoryRef, commit: str, path: str) -> str:
    return f"https://github.com/{ref.full_name}/blob/{commit}/{path}"


def _provenance(ref: RepositoryRef, commit: str, path: str, blob_sha: str) -> dict[str, Any]:
    return {
        "repository": ref.full_name,
        "commit": commit,
        "path": path,
        "blob_sha": blob_sha,
        "url": _blob_url(ref, commit, path),
    }


def _fingerprint(ref: RepositoryRef, commit: str, listing: RepositoryListing) -> str:
    """Identity of a capture: the commit plus exactly which blobs were listed."""
    digest = hashlib.sha256()
    digest.update(f"{ref.full_name}@{commit}\n".encode())
    for entry in sorted(listing.files, key=lambda item: item.path):
        digest.update(f"{entry.path}\t{entry.sha}\n".encode())
    return digest.hexdigest()


def _reuse(
    session: Session,
    snapshot: Snapshot,
    listing: RepositoryListing,
) -> RepositorySnapshotOutcome:
    documents = tuple(
        session.scalars(
            sa.select(Document)
            .where(Document.snapshot_id == snapshot.id)
            .order_by(Document.path)
        ).all()
    )
    return RepositorySnapshotOutcome(
        snapshot=snapshot, listing=listing, documents=documents, manifest={}, reused=True
    )


def _manifest(
    ref: RepositoryRef,
    commit: str,
    listing: RepositoryListing,
    documents: list[Document],
    excluded: list[ExcludedFile],
    fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "repository": ref.full_name,
        "commit": commit,
        "listing": {
            "file_count": len(listing.files),
            "truncated": listing.truncated,
            "recovered_from_truncation": listing.recovered_from_truncation,
            "tree_requests": listing.tree_requests,
            "submodules": list(listing.submodules),
            "symlinks": list(listing.symlinks),
        },
        "documents": [
            {
                "path": document.path,
                "url": document.uri,
                "language": document.code_language.value if document.code_language else None,
                "byte_size": document.byte_size,
                "sha256": document.body_sha256,
            }
            for document in documents
        ],
        "excluded": [
            {"path": entry.path, "reason": entry.reason.value, "detail": entry.detail}
            for entry in excluded
        ],
    }


def _digest(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()
