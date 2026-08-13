"""Capturing a repository at one commit.

Same invariant as the web snapshot: bytes in content-addressed storage, metadata and
provenance in MySQL, and a capture that is immutable once taken. The extra obligation here
is that an excluded file is not merely dropped from the corpus — it is never downloaded.
"""

from __future__ import annotations

import base64
import json

import pytest
import sqlalchemy as sa
from conftest import make_project
from ingestion_support import StubSite, public_resolver
from sqlalchemy.orm import Session

from app.db.models import Artifact, Chunk, Document, Source
from app.db.models.enums import ArtifactKind, CodeLanguage, DocumentKind, SnapshotStatus, SourceKind
from app.ingestion.github.client import GitHubClient
from app.ingestion.github.refs import parse_repository
from app.ingestion.github.snapshot import capture_repository_snapshot
from app.ingestion.web.fetcher import SafeFetcher
from app.ingestion.web.safety import AddressGuard
from app.storage.content_store import ContentAddressedStore

REF = parse_repository("octo/gateway")
COMMIT = "9f2c1b7a4e5d6f80123456789abcdef012345678"

README = b"# Gateway\n\nThe gateway service routes requests to the supervisor.\n"
APP = b"def build_gateway(config):\n    return Gateway(config)\n"
SERVICE = b"export function start(): void {}\n"
SECRET = b"AWS_SECRET_ACCESS_KEY=hunter2\n"
LOGO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def sha(seed: str) -> str:
    return (seed * 40)[:40]


def api(path: str) -> str:
    return f"https://api.github.com{path}"


def blob_entry(path: str, seed: str, size: int) -> dict:
    return {"path": path, "mode": "100644", "type": "blob", "sha": sha(seed), "size": size}


@pytest.fixture
def site() -> StubSite:
    stub = StubSite()
    stub.add(
        api("/repos/octo/gateway"),
        json.dumps({"full_name": "octo/gateway", "default_branch": "main", "private": False}),
        content_type="application/json",
    )
    stub.add(
        api("/repos/octo/gateway/commits/main"),
        json.dumps({"sha": COMMIT}),
        content_type="application/json",
    )
    stub.add(
        api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
        json.dumps(
            {
                "sha": sha("t"),
                "truncated": False,
                "tree": [
                    blob_entry("README.md", "a", len(README)),
                    blob_entry("src/app.py", "b", len(APP)),
                    blob_entry("src/service.ts", "c", len(SERVICE)),
                    blob_entry(".env", "d", len(SECRET)),
                    blob_entry("assets/logo.png", "e", len(LOGO)),
                    blob_entry("node_modules/left-pad/index.js", "f", 100),
                ],
            }
        ),
        content_type="application/json",
    )
    for seed, payload in (("a", README), ("b", APP), ("c", SERVICE), ("d", SECRET), ("e", LOGO)):
        stub.add(
            api(f"/repos/octo/gateway/git/blobs/{sha(seed)}"),
            json.dumps(
                {
                    "sha": sha(seed),
                    "size": len(payload),
                    "encoding": "base64",
                    "content": base64.b64encode(payload).decode(),
                }
            ),
            content_type="application/json",
        )
    return stub


@pytest.fixture
def store(tmp_path) -> ContentAddressedStore:
    return ContentAddressedStore(tmp_path / "artifacts")


@pytest.fixture
def source(db: Session) -> Source:
    project = make_project(db)
    row = Source(
        project_id=project.id,
        kind=SourceKind.GITHUB_REPO,
        locator="octo/gateway",
        locator_fingerprint="b" * 64,
    )
    db.add(row)
    db.flush()
    return row


def client_for(site: StubSite) -> GitHubClient:
    return GitHubClient(
        SafeFetcher(
            guard=AddressGuard(resolver=public_resolver("api.github.com")),
            transport=site.transport,
        )
    )


def capture(db: Session, store: ContentAddressedStore, source: Source, site: StubSite, **kwargs):
    return capture_repository_snapshot(
        db, store, source=source, client=client_for(site), ref=REF, **kwargs
    )


def test_only_the_admitted_files_become_documents(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)

    assert [document.path for document in outcome.documents] == [
        "README.md",
        "src/app.py",
        "src/service.ts",
    ]
    assert all(document.kind is DocumentKind.REPO_FILE for document in outcome.documents)


def test_an_excluded_file_is_never_downloaded(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    """A checked-in secret must not be fetched, not merely dropped after fetching."""
    capture(db, store, source, site)

    requested = site.targets_contacted()
    assert f"/repos/octo/gateway/git/blobs/{sha('d')}" not in requested
    assert f"/repos/octo/gateway/git/blobs/{sha('e')}" not in requested


def test_every_exclusion_is_recorded_with_its_reason(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)
    reasons = {entry.path: entry.reason.value for entry in outcome.excluded}

    assert reasons[".env"] == "secret"
    assert reasons["assets/logo.png"] == "binary"
    assert reasons["node_modules/left-pad/index.js"] == "generated"


def test_the_snapshot_is_pinned_to_the_commit(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)

    assert outcome.snapshot.commit_sha == COMMIT
    assert outcome.snapshot.status is SnapshotStatus.READY
    assert outcome.snapshot.document_count == 3


def test_the_source_bytes_live_in_the_store(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)
    db.commit()

    document = next(item for item in outcome.documents if item.path == "src/app.py")
    artifact = db.get(Artifact, document.raw_artifact_id)
    assert artifact is not None
    assert artifact.kind is ArtifactKind.RAW_REPO_FILE
    assert store.read_bytes(artifact.storage_path) == APP


def test_each_document_is_addressed_by_the_pinned_blob_url(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)

    document = next(item for item in outcome.documents if item.path == "src/app.py")
    assert document.uri == f"https://github.com/octo/gateway/blob/{COMMIT}/src/app.py"


def test_the_language_of_each_file_is_recorded(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)
    languages = {document.path: document.code_language for document in outcome.documents}

    assert languages["src/app.py"] is CodeLanguage.PYTHON
    assert languages["src/service.ts"] is CodeLanguage.TYPESCRIPT
    assert languages["README.md"] is None


def test_citations_name_the_repository_the_commit_and_the_lines(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)

    assert f"octo/gateway@{COMMIT}/src/app.py#L1-L2" in outcome.citations


def test_files_are_chunked_so_a_range_of_lines_can_be_quoted(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    capture(db, store, source, site)

    chunks = db.scalars(sa.select(Chunk)).all()
    assert chunks
    assert any(chunk.anchor == "L1-L2" for chunk in chunks)


def test_the_manifest_records_the_commit_and_what_was_left_out(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    outcome = capture(db, store, source, site)
    db.commit()

    artifact = db.get(Artifact, outcome.snapshot.manifest_artifact_id)
    assert artifact is not None
    manifest = json.loads(store.read_bytes(artifact.storage_path))
    assert manifest["repository"] == "octo/gateway"
    assert manifest["commit"] == COMMIT
    assert manifest["listing"]["truncated"] is False
    assert {entry["path"] for entry in manifest["excluded"]} == {
        ".env",
        "assets/logo.png",
        "node_modules/left-pad/index.js",
    }


def test_capturing_the_same_commit_twice_converges(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    first = capture(db, store, source, site)
    db.commit()
    second = capture(db, store, source, site)
    db.commit()

    assert second.snapshot.id == first.snapshot.id
    assert second.reused is True
    assert db.scalar(sa.select(sa.func.count()).select_from(Document)) == 3


def test_content_that_turns_out_to_be_binary_is_dropped_after_download(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    site.add(
        api(f"/repos/octo/gateway/git/blobs/{sha('b')}"),
        json.dumps(
            {
                "sha": sha("b"),
                "size": 8,
                "encoding": "base64",
                "content": base64.b64encode(b"\x00\x01\x02binary").decode(),
            }
        ),
        content_type="application/json",
    )

    outcome = capture(db, store, source, site)

    assert "src/app.py" not in [document.path for document in outcome.documents]
    assert any(entry.path == "src/app.py" for entry in outcome.excluded)


def test_a_git_lfs_pointer_is_dropped_after_download(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 900\n"
    site.add(
        api(f"/repos/octo/gateway/git/blobs/{sha('b')}"),
        json.dumps(
            {
                "sha": sha("b"),
                "size": len(pointer),
                "encoding": "base64",
                "content": base64.b64encode(pointer).decode(),
            }
        ),
        content_type="application/json",
    )

    outcome = capture(db, store, source, site)
    reasons = {entry.path: entry.reason.value for entry in outcome.excluded}

    assert reasons["src/app.py"] == "git_lfs_pointer"


def test_a_truncated_listing_is_surfaced_rather_than_hidden(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    site.add(
        api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
        json.dumps({"sha": sha("t"), "truncated": True, "tree": []}),
        content_type="application/json",
    )
    site.add(
        api(f"/repos/octo/gateway/git/trees/{COMMIT}"),
        json.dumps(
            {
                "sha": sha("t"),
                "truncated": True,
                "tree": [blob_entry("README.md", "a", len(README))],
            }
        ),
        content_type="application/json",
    )

    outcome = capture(db, store, source, site)

    assert outcome.listing.truncated is True
    assert outcome.manifest["listing"]["truncated"] is True


def test_no_repository_script_is_ever_executed(
    db: Session, store: ContentAddressedStore, source: Source, site: StubSite
) -> None:
    """Reading a repository is parsing, never running. Only the API is contacted."""
    capture(db, store, source, site)

    assert set(site.hosts_contacted()) == {"api.github.com"}
