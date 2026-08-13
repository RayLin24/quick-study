"""The work each phase does, and the seam that lets a later phase replace it.

Every business step of the graph is a plain function of ``(state, call)`` collected in
``TutorialNodes``. The defaults here are deterministic stubs: they move the pipeline
through its phases and produce the right shapes without fetching a page, running an
analyser or calling a model. Ingestion, retrieval, generation, diagrams and the quality
gate each replace one field of this record when they land, and the graph, the checkpointer
and the step contract do not change when they do.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.clock import utcnow
from app.db.models.enums import ChapterStatus
from app.workflows.tutorial.recording import NodeCall, NodeOutcome
from app.workflows.tutorial.state import (
    ChapterDraft,
    DiagramDraft,
    OutlineChapter,
    TutorialState,
    chapter_draft,
    input_digest,
)

NodeFunction = Callable[[TutorialState, NodeCall], NodeOutcome]

#: What the stub outline proposes. Real outlines come from the generation phase.
STUB_CHAPTER_TITLES: tuple[str, ...] = (
    "What this project is",
    "How it fits together",
    "Working through an example",
)


def discover_sources(state: TutorialState, call: NodeCall) -> NodeOutcome:
    """Turn the approved request into the concrete list of sources to read."""
    return NodeOutcome(update={"discovered": list(state["request"]["sources"])})


def capture_snapshots(state: TutorialState, call: NodeCall) -> NodeOutcome:
    """Pin each source to an immutable snapshot every later citation points at."""
    snapshots = [f"snapshot-{ref['fingerprint'][:16]}" for ref in state.get("discovered", [])]
    return NodeOutcome(update={"snapshots": snapshots})


def parse_documents(state: TutorialState, call: NodeCall) -> NodeOutcome:
    snapshots = state.get("snapshots", [])
    return NodeOutcome(
        update={"corpus": {"snapshots": len(snapshots), "documents": len(snapshots)}}
    )


def index_corpus(state: TutorialState, call: NodeCall) -> NodeOutcome:
    corpus = state.get("corpus", {})
    return NodeOutcome(update={"index": {"documents": corpus.get("documents", 0)}})


def analyse_corpus(state: TutorialState, call: NodeCall) -> NodeOutcome:
    return NodeOutcome(update={"analysis": {"modules": [], "symbols": 0}})


def draft_outline(state: TutorialState, call: NodeCall) -> NodeOutcome:
    """Propose a table of contents. Each rejection produces the next version."""
    previous = state.get("outline")
    version = (previous["version"] + 1) if previous else 1
    chapters: list[OutlineChapter] = [
        {
            "slug": _slug(title),
            "title": title,
            "ordinal": ordinal,
            "summary": "",
        }
        for ordinal, title in enumerate(STUB_CHAPTER_TITLES)
    ]
    return NodeOutcome(
        update={
            "outline": {
                "version": version,
                "title": state["request"]["title"],
                "summary": "",
                "chapters": chapters,
            }
        }
    )


def write_chapters(state: TutorialState, call: NodeCall) -> NodeOutcome:
    outline = state["outline"]
    drafts: dict[str, ChapterDraft] = {
        chapter["slug"]: chapter_draft(
            slug=chapter["slug"],
            title=chapter["title"],
            ordinal=chapter["ordinal"],
            status=ChapterStatus.DRAFTED,
            content_hash=input_digest([outline["version"], chapter["slug"]]),
        )
        for chapter in outline["chapters"]
    }
    return NodeOutcome(update={"chapters": drafts})


def draw_diagrams(state: TutorialState, call: NodeCall) -> NodeOutcome:
    diagrams: dict[str, DiagramDraft] = {
        slug: {
            "chapter_slug": slug,
            "kind": "flowchart",
            "status": "pending",
            "mermaid_hash": None,
        }
        for slug in state.get("chapters", {})
    }
    return NodeOutcome(update={"diagrams": diagrams})


def validate_tutorial(state: TutorialState, call: NodeCall) -> NodeOutcome:
    return NodeOutcome(update={"validation": {"passed": True, "findings": []}})


def publish_tutorial(state: TutorialState, call: NodeCall) -> NodeOutcome:
    bundle = input_digest(
        {
            "chapters": sorted(state.get("chapters", {})),
            "outline": state.get("outline", {}).get("version"),
            "pipeline_version": call.pipeline_version,
        }
    )
    return NodeOutcome(
        update={
            "publication": {"bundle_hash": bundle, "published_at": utcnow().isoformat()}
        }
    )


@dataclass(frozen=True, slots=True)
class TutorialNodes:
    """The injectable body of every phase of the graph."""

    discover: NodeFunction = discover_sources
    snapshot: NodeFunction = capture_snapshots
    parse: NodeFunction = parse_documents
    index: NodeFunction = index_corpus
    analyze: NodeFunction = analyse_corpus
    outline: NodeFunction = draft_outline
    chapters: NodeFunction = write_chapters
    diagrams: NodeFunction = draw_diagrams
    validate: NodeFunction = validate_tutorial
    publish: NodeFunction = publish_tutorial

    def for_node(self, name: str) -> NodeFunction:
        return getattr(self, name)


def _slug(title: str) -> str:
    kept = [character.lower() if character.isalnum() else "-" for character in title]
    return "-".join(part for part in "".join(kept).split("-") if part)
