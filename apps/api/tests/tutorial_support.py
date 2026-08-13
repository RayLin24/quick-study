"""Builders for tutorial documents, diagrams and evidence.

Each helper produces the smallest valid value and lets a test override exactly the field it
is about, so a test that is meant to fail validation fails for the reason it names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.db.models.enums import CitationKind, ClaimKind
from app.retrieval import SearchHit, SearchKind, SearchQuery
from app.tutorial.mermaid_ir import (
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    EdgeKind,
    MermaidDiagram,
    NodeRole,
)
from app.tutorial.schema import (
    AtomicFact,
    CodeBlock,
    DiagramBlock,
    EvidenceCitation,
    Exercise,
    GlossaryTerm,
    MarkdownBlock,
    TutorialChapter,
    TutorialDocument,
    TutorialMetadata,
)

SNAPSHOT_ID = "snapshot-1"
WEB_LOCATOR = "https://docs.example.test/deploy#chunk-0"
REPO_LOCATOR = "octocat/hello-world@0123456789abcdef0123456789abcdef01234567/src/gateway.py#L10-L24"


def citation(**overrides: Any) -> EvidenceCitation:
    payload: dict[str, Any] = {
        "id": "e1",
        "kind": CitationKind.WEB,
        "snapshot_id": SNAPSHOT_ID,
        "locator": WEB_LOCATOR,
        "title": "Gateway deployment guide",
        "quote": "Deploy the gateway service behind the supervisor process.",
        "retrieved_at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return EvidenceCitation(**payload)


def repo_citation(**overrides: Any) -> EvidenceCitation:
    payload: dict[str, Any] = {
        "id": "e2",
        "kind": CitationKind.REPO,
        "snapshot_id": SNAPSHOT_ID,
        "locator": REPO_LOCATOR,
        "title": "gateway.build_gateway",
        "start_line": 10,
        "end_line": 24,
    }
    payload.update(overrides)
    return EvidenceCitation(**payload)


def fact(**overrides: Any) -> AtomicFact:
    payload: dict[str, Any] = {
        "id": "f1",
        "statement": "The gateway runs behind a supervisor process.",
        "kind": ClaimKind.FACT,
        "citation_ids": ("e1",),
    }
    payload.update(overrides)
    return AtomicFact(**payload)


def markdown_block(**overrides: Any) -> MarkdownBlock:
    payload: dict[str, Any] = {
        "markdown": "The gateway runs behind a supervisor process.",
        "citation_ids": ("e1",),
    }
    payload.update(overrides)
    return MarkdownBlock(**payload)


def code_block(**overrides: Any) -> CodeBlock:
    payload: dict[str, Any] = {
        "language": "python",
        "code": "gateway = build_gateway(config)",
        "caption": "Build the gateway",
        "citation_ids": ("e2",),
    }
    payload.update(overrides)
    return CodeBlock(**payload)


def diagram(**overrides: Any) -> MermaidDiagram:
    payload: dict[str, Any] = {
        "slug": "gateway-architecture",
        "kind": DiagramKind.ARCHITECTURE,
        "title": "Gateway architecture",
        "nodes": (
            DiagramNode(id="Gateway", label="Gateway", role=NodeRole.SERVICE, citation_ids=("e2",)),
            DiagramNode(
                id="Supervisor",
                label="Supervisor",
                role=NodeRole.SERVICE,
                citation_ids=("e1",),
            ),
        ),
        "edges": (
            DiagramEdge(
                source="Supervisor",
                target="Gateway",
                label="starts",
                kind=EdgeKind.CALL,
                citation_ids=("e1",),
            ),
        ),
    }
    payload.update(overrides)
    return MermaidDiagram(**payload)


def diagram_block(**overrides: Any) -> DiagramBlock:
    payload: dict[str, Any] = {"diagram": diagram(), "caption": "How the parts fit together"}
    payload.update(overrides)
    return DiagramBlock(**payload)


def exercise(**overrides: Any) -> Exercise:
    payload: dict[str, Any] = {
        "id": "x1",
        "prompt": "Start the gateway behind a supervisor and confirm it restarts.",
        "hints": ("Read the deployment guide first.",),
        "citation_ids": ("e1",),
    }
    payload.update(overrides)
    return Exercise(**payload)


def glossary_term(**overrides: Any) -> GlossaryTerm:
    payload: dict[str, Any] = {
        "term": "supervisor",
        "definition": "The process that starts and restarts the gateway.",
        "citation_ids": ("e1",),
    }
    payload.update(overrides)
    return GlossaryTerm(**payload)


def chapter(**overrides: Any) -> TutorialChapter:
    payload: dict[str, Any] = {
        "slug": "deployment",
        "ordinal": 0,
        "title": "Deploying the gateway",
        "summary": "How the gateway is deployed and supervised.",
        "blocks": (markdown_block(), code_block()),
        "facts": (fact(),),
        "exercises": (exercise(),),
    }
    payload.update(overrides)
    return TutorialChapter(**payload)


def metadata(**overrides: Any) -> TutorialMetadata:
    payload: dict[str, Any] = {
        "project_id": "project-1",
        "run_id": "run-1",
        "title": "Gateway tutorial",
        "slug": "gateway-tutorial",
        "description": "A tour of the gateway service.",
        "snapshot_ids": (SNAPSHOT_ID,),
    }
    payload.update(overrides)
    return TutorialMetadata(**payload)


def document(**overrides: Any) -> TutorialDocument:
    payload: dict[str, Any] = {
        "metadata": metadata(),
        "chapters": (chapter(),),
        "citations": (citation(), repo_citation()),
        "glossary": (glossary_term(),),
    }
    payload.update(overrides)
    return TutorialDocument(**payload)


def search_hit(**overrides: Any) -> SearchHit:
    payload: dict[str, Any] = {
        "kind": SearchKind.DOCUMENT_CHUNK,
        "score": 1.0,
        "project_id": "project-1",
        "snapshot_id": SNAPSHOT_ID,
        "locator": WEB_LOCATOR,
        "title": "Gateway deployment guide",
        "excerpt": "Deploy the gateway service behind the supervisor process.",
        "document_id": "document-1",
        "chunk_id": "chunk-1",
    }
    payload.update(overrides)
    return SearchHit(**payload)


class FakeSearchService:
    """Answers each query from a canned table and records what it was asked.

    Retrieval itself is already covered by its own tests; what matters here is which
    queries the evidence builder derives and how it merges the results.
    """

    def __init__(
        self,
        results: Mapping[str, Sequence[SearchHit]] | None = None,
        *,
        default: Iterable[SearchHit] = (),
    ) -> None:
        self._results = {text: tuple(hits) for text, hits in (results or {}).items()}
        self._default = tuple(default)
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[SearchHit]:
        self.queries.append(query)
        return list(self._results.get(query.text, self._default))[: query.limit]

    @property
    def query_texts(self) -> list[str]:
        return [query.text for query in self.queries]
