"""Turning a crawl into an immutable, citable snapshot.

The invariant under test is the one the whole system rests on: payloads live in
content-addressed storage and MySQL holds only paths, digests and provenance. A snapshot
is never rewritten, so re-running the same crawl has to converge on the same rows rather
than accumulate new ones.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from conftest import make_project
from sqlalchemy.orm import Session

from app.db.models import Artifact, Chunk, Document, Source
from app.db.models.enums import ArtifactKind, DocumentKind, SnapshotStatus, SourceKind
from app.ingestion.web.crawler import CrawledPage, CrawlResult, SkippedUrl, SkipReason
from app.ingestion.web.snapshot import persist_web_snapshot
from app.ingestion.web.urls import SiteScope, normalize_url
from app.storage.content_store import ContentAddressedStore

FETCHED_AT = datetime(2026, 3, 4, 10, 30, tzinfo=UTC)


def html(title: str, extra: str = "") -> bytes:
    body = "".join(
        f"<p>Paragraph {index} of {title} explains how the supervisor reloads its "
        f"configuration without downtime.</p>"
        for index in range(6)
    )
    return (
        f"<html><head><title>{title}</title></head><body><article>"
        f"<h1>{title}</h1>{body}<h2>Requirements</h2><p>Python 3.12 or newer.</p>{extra}"
        f"</article></body></html>"
    ).encode()


def crawled(path: str, title: str, extra: str = "") -> CrawledPage:
    url = normalize_url(f"https://docs.example.test{path}")
    return CrawledPage(
        url=url,
        requested_url=url,
        status_code=200,
        content=html(title, extra),
        media_type="text/html",
        charset="utf-8",
        fetched_at=FETCHED_AT,
        depth=0,
        discovered_via="sitemap",
        addresses=("93.184.216.34",),
    )


def crawl_result(*pages: CrawledPage, skipped: tuple[SkippedUrl, ...] = ()) -> CrawlResult:
    seed = normalize_url("https://docs.example.test/")
    from app.ingestion.web.robots import RobotsPolicy

    return CrawlResult(
        seed=seed,
        scope=SiteScope.from_seed(seed),
        robots=RobotsPolicy.allow_all(),
        started_at=FETCHED_AT,
        finished_at=FETCHED_AT,
        pages=pages,
        skipped=skipped,
        total_bytes=sum(len(page.content) for page in pages),
    )


@pytest.fixture
def store(tmp_path: Path) -> ContentAddressedStore:
    return ContentAddressedStore(tmp_path / "artifacts")


@pytest.fixture
def source(db: Session) -> Source:
    project = make_project(db)
    row = Source(
        project_id=project.id,
        kind=SourceKind.WEBSITE,
        locator="https://docs.example.test/",
        locator_fingerprint="a" * 64,
        display_name="Example Docs",
    )
    db.add(row)
    db.flush()
    return row


def test_each_unique_page_becomes_one_document(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    outcome = persist_web_snapshot(
        db,
        store,
        source=source,
        crawl=crawl_result(crawled("/install", "Install"), crawled("/configure", "Configure")),
    )

    assert len(outcome.documents) == 2
    assert {document.uri for document in outcome.documents} == {
        "https://docs.example.test/install",
        "https://docs.example.test/configure",
    }
    assert all(document.kind is DocumentKind.WEB_PAGE for document in outcome.documents)


def test_the_raw_bytes_live_in_the_store_and_only_metadata_in_the_database(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    page = crawled("/install", "Install")

    outcome = persist_web_snapshot(db, store, source=source, crawl=crawl_result(page))
    db.commit()

    document = outcome.documents[0]
    raw = db.get(Artifact, document.raw_artifact_id)
    assert raw is not None
    assert raw.kind is ArtifactKind.RAW_HTML
    assert store.read_bytes(raw.storage_path) == page.content
    stored_rows = str(db.execute(sa.text("SELECT * FROM artifacts")).mappings().all())
    assert "<html>" not in stored_rows


def test_the_normalised_markdown_is_stored_and_indexed_as_body_text(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    outcome = persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(crawled("/install", "Install"))
    )

    document = outcome.documents[0]
    normalized = db.get(Artifact, document.normalized_artifact_id)
    assert normalized is not None
    assert normalized.kind is ArtifactKind.NORMALIZED_CORPUS
    assert b"# Install" in store.read_bytes(normalized.storage_path)
    assert "supervisor" in document.body_text
    assert "#" not in document.body_text


def test_documents_are_chunked_with_anchors_a_citation_can_use(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(crawled("/install", "Install"))
    )

    chunks = db.scalars(sa.select(Chunk).order_by(Chunk.ordinal)).all()
    assert [chunk.anchor for chunk in chunks] == ["install", "requirements"]
    assert chunks[1].heading_path == "Install > Requirements"
    assert all(len(chunk.sha256) == 64 for chunk in chunks)


def test_every_page_gets_a_citation_pinned_to_the_fetch_instant(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    outcome = persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(crawled("/install", "Install"))
    )

    assert outcome.citations[0].startswith("https://docs.example.test/install#install@")
    assert outcome.citations[0].endswith("@2026-03-04T10:30:00Z")


def test_a_page_served_twice_under_different_urls_is_stored_once(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    first = crawled("/install", "Install")
    mirror = crawled("/en/install", "Install")

    outcome = persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(first, mirror)
    )

    assert len(outcome.documents) == 1
    assert outcome.duplicates and outcome.duplicates[0].original_key == str(first.url)


def test_a_page_with_nothing_extractable_is_recorded_but_not_indexed(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    empty = CrawledPage(
        url=normalize_url("https://docs.example.test/empty"),
        requested_url=normalize_url("https://docs.example.test/empty"),
        status_code=200,
        content=b"",
        media_type="text/html",
        charset="utf-8",
        fetched_at=FETCHED_AT,
        depth=0,
        discovered_via="link",
    )

    outcome = persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(crawled("/install", "Install"), empty)
    )

    assert len(outcome.documents) == 1
    assert any(entry["outcome"] == "empty" for entry in outcome.manifest["pages"])


def test_the_snapshot_row_summarises_what_was_captured(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    outcome = persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(crawled("/install", "Install"))
    )

    snapshot = outcome.snapshot
    assert snapshot.status is SnapshotStatus.READY
    assert snapshot.document_count == 1
    assert snapshot.byte_size > 0
    assert snapshot.captured_at is not None
    assert snapshot.commit_sha is None
    assert len(snapshot.fingerprint) == 64


def test_the_manifest_records_where_every_byte_came_from(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    skipped = (
        SkippedUrl(normalize_url("https://evil.test/x"), SkipReason.OUT_OF_SCOPE),
    )

    outcome = persist_web_snapshot(
        db,
        store,
        source=source,
        crawl=crawl_result(crawled("/install", "Install"), skipped=skipped),
    )
    db.commit()

    manifest_artifact = db.get(Artifact, outcome.snapshot.manifest_artifact_id)
    assert manifest_artifact is not None
    assert manifest_artifact.kind is ArtifactKind.SNAPSHOT_MANIFEST
    manifest = json.loads(store.read_bytes(manifest_artifact.storage_path))
    entry = manifest["pages"][0]
    assert entry["url"] == "https://docs.example.test/install"
    assert entry["fetched_at"] == "2026-03-04T10:30:00Z"
    assert entry["addresses"] == ["93.184.216.34"]
    assert len(entry["raw_sha256"]) == 64
    assert manifest["skipped"][0]["reason"] == "out_of_scope"


def test_capturing_the_same_crawl_twice_converges_on_one_snapshot(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    """Steps are delivered at least once, so a repeat must add nothing."""
    crawl = crawl_result(crawled("/install", "Install"), crawled("/configure", "Configure"))

    first = persist_web_snapshot(db, store, source=source, crawl=crawl)
    db.commit()
    second = persist_web_snapshot(db, store, source=source, crawl=crawl)
    db.commit()

    assert second.snapshot.id == first.snapshot.id
    assert db.scalar(sa.select(sa.func.count()).select_from(Document)) == 2
    assert second.reused is True


def test_a_changed_site_produces_a_new_snapshot_rather_than_editing_the_old_one(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    first = persist_web_snapshot(
        db, store, source=source, crawl=crawl_result(crawled("/install", "Install"))
    )
    db.commit()

    second = persist_web_snapshot(
        db,
        store,
        source=source,
        crawl=crawl_result(crawled("/install", "Install", extra="<p>Also configure TLS.</p>")),
    )
    db.commit()

    assert second.snapshot.id != first.snapshot.id
    assert second.snapshot.fingerprint != first.snapshot.fingerprint


def test_the_producing_step_is_recorded_on_every_artifact(
    db: Session, store: ContentAddressedStore, source: Source
) -> None:
    from conftest import make_run

    from app.db.models import Project
    from app.db.models.enums import RunPhase
    from app.runs.steps import ensure_step

    run = make_run(db, project=db.get(Project, source.project_id))
    step = ensure_step(db, run=run, name="snapshot", phase=RunPhase.SNAPSHOT)

    persist_web_snapshot(
        db,
        store,
        source=source,
        crawl=crawl_result(crawled("/install", "Install")),
        run_id=run.id,
        step_id=step.id,
    )

    artifacts = db.scalars(sa.select(Artifact)).all()
    assert artifacts and all(artifact.step_id == step.id for artifact in artifacts)
