"""Real-GitHub ingestion smoke test against langchain-ai/langchain.

Runs the production capture path (commit pin -> tree walk -> filter -> blob download
-> content-addressed artifacts -> documents/chunks/citations -> manifest) with tight
budgets so the unauthenticated 60 req/h API quota is respected.
"""
import json
import sys

sys.path.insert(0, "apps/api")

from app.db.session import get_session_factory
from app.db.models import Source
from app.ingestion.github.client import GitHubClient
from app.ingestion.github.refs import parse_repository
from app.ingestion.github.snapshot import capture_repository_snapshot
from app.ingestion.github.tree import TreeLimits
from app.ingestion.web.fetcher import SafeFetcher
from app.storage.content_store import build_content_store
import sqlalchemy as sa

PROJECT_ID = "6fa235b103234a8a9648dfe9bf6cc1b4"
SOURCE_ID = "b6b5a0e2e26349da942cc9ad5cbb04d5"

ref = parse_repository("https://github.com/langchain-ai/langchain")
factory = get_session_factory()
store = build_content_store()

with SafeFetcher() as fetcher:
    client = GitHubClient(fetcher)
    with factory() as session:
        source = session.get(Source, SOURCE_ID)
        outcome = capture_repository_snapshot(
            session,
            store,
            source=source,
            client=client,
            ref=ref,
            tree_limits=TreeLimits(max_requests=15, max_files=25, max_depth=8),
        )
        session.commit()

listing = outcome.listing
by_reason: dict[str, int] = {}
for entry in outcome.excluded:
    by_reason[entry.reason.value] = by_reason.get(entry.reason.value, 0) + 1

print(json.dumps({
    "commit": outcome.snapshot.commit_sha[:12],
    "fingerprint": outcome.snapshot.fingerprint[:16],
    "status": outcome.snapshot.status.value,
    "document_count": outcome.snapshot.document_count,
    "byte_size": outcome.snapshot.byte_size,
    "truncated": listing.truncated,
    "recovered_from_truncation": listing.recovered_from_truncation,
    "tree_requests": listing.tree_requests,
    "submodules": len(listing.submodules),
    "symlinks": len(listing.symlinks),
    "excluded_by_reason": by_reason,
    "first_documents": [
        {"path": d.path, "lang": d.code_language.value if d.code_language else None,
         "bytes": d.byte_size, "url": d.uri}
        for d in outcome.documents[:8]
    ],
    "citation_samples": list(outcome.citations[:4]),
    "manifest_artifact_id": outcome.snapshot.manifest_artifact_id,
}, ensure_ascii=False, indent=1))
