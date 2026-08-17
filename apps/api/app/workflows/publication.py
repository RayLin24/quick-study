"""Write the human-facing rows a suspended or finished run leaves behind."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.models import Artifact, Outline, Run
from app.db.models.enums import ArtifactKind, OutlineStatus
from app.storage.artifacts import write_artifact
from app.storage.content_store import ContentAddressedStore


def persist_pending_outline(
    session: Session, run: Run, interrupt: Mapping[str, Any]
) -> Outline:
    """Store the outline the reviewer is about to see, so the HTTP API can name it."""
    draft = interrupt.get("outline") or {}
    version = int(draft.get("version") or 1)
    existing = session.scalars(
        sa.select(Outline).where(Outline.run_id == run.id, Outline.version == version)
    ).one_or_none()
    title = str(draft.get("title") or "")
    summary = str(draft.get("summary") or "")
    structure = dict(draft)
    if existing is None:
        existing = Outline(
            project_id=run.project_id,
            run_id=run.id,
            version=version,
            status=OutlineStatus.PENDING_APPROVAL,
            title=title,
            summary=summary,
            structure=structure,
        )
        session.add(existing)
        session.flush()
        return existing
    existing.status = OutlineStatus.PENDING_APPROVAL
    existing.title = title
    existing.summary = summary
    existing.structure = structure
    return existing


def persist_export_bundle(
    session: Session,
    store: ContentAddressedStore,
    run: Run,
    result: Mapping[str, Any],
) -> Artifact:
    """Write a Markdown zip so the export endpoint has something to stream."""
    payload = build_markdown_bundle(result)
    return write_artifact(
        session,
        store,
        payload,
        project_id=run.project_id,
        kind=ArtifactKind.EXPORT_BUNDLE,
        media_type="application/zip",
        run_id=run.id,
    )


def build_markdown_bundle(result: Mapping[str, Any]) -> bytes:
    outline = result.get("outline") or {}
    title = str(outline.get("title") or "Tutorial")
    chapters = list(outline.get("chapters") or [])
    drafts = result.get("chapters") or {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        index = [f"# {title}", ""]
        for chapter in sorted(chapters, key=lambda item: int(item.get("ordinal") or 0)):
            slug = str(chapter.get("slug") or "chapter")
            heading = str(chapter.get("title") or slug)
            index.append(f"- {heading}")
            draft = drafts.get(slug) if isinstance(drafts, Mapping) else None
            body = f"# {heading}\n"
            if isinstance(draft, Mapping):
                markdown = str(draft.get("markdown") or "").strip()
                if markdown:
                    body = markdown if markdown.lstrip().startswith("#") else f"# {heading}\n\n{markdown}\n"
                elif draft.get("summary"):
                    body += f"\n{draft['summary']}\n"
            archive.writestr(f"{slug}.md", body)
        archive.writestr("README.md", "\n".join(index) + "\n")
    return buffer.getvalue()


def latest_outline(session: Session, run_id: str) -> Outline | None:
    return session.scalars(
        sa.select(Outline)
        .where(Outline.run_id == run_id)
        .order_by(Outline.version.desc())
    ).first()


def outline_payload(outline: Outline | None) -> dict[str, Any] | None:
    if outline is None:
        return None
    chapters = list((outline.structure or {}).get("chapters") or [])
    return {
        "id": outline.id,
        "version": outline.version,
        "title": outline.title,
        "summary": outline.summary,
        "status": outline.status.value,
        "chapters": chapters,
    }
