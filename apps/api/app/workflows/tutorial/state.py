"""The state the tutorial generation graph carries between nodes.

Everything here is JSON-shaped so it survives a round trip through the checkpointer, and
the invariants of the pipeline live in the reducers rather than in the nodes:

* ``phase`` only ever moves forward, with the single documented exception of a rejected
  outline going back to be regenerated;
* a chapter a reviewer locked is never replaced by a regeneration;
* node telemetry and model usage accumulate instead of being overwritten, so a retried
  node adds a record rather than hiding the earlier attempt.

The state is a convenience for the graph, not the record of truth: MySQL holds the runs,
steps, outlines, chapters and approvals a human or an API caller reads.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Any, Final, TypedDict

from app.clock import utcnow
from app.db.models.enums import (
    ChapterStatus,
    CodeLanguage,
    LengthPreset,
    ReaderLevel,
    RunPhase,
    SourceKind,
)
from app.runs.state_machine import assert_phase_change

#: Bumped whenever a change to the graph makes older artifacts non-comparable. It is
#: recorded on every node so a chapter can always be traced to the pipeline that wrote it.
PIPELINE_VERSION: Final = "1"

ZERO_USAGE: Final[dict[str, Any]] = {"tokens_in": 0, "tokens_out": 0, "cost_usd": "0"}


class SourceRef(TypedDict):
    """A source the run is allowed to read, fingerprinted so it can be deduplicated."""

    kind: str
    locator: str
    fingerprint: str


class TutorialRequest(TypedDict):
    """What the reviewer asked for. Fixed for the lifetime of the run."""

    title: str
    output_language: str
    reader_level: str
    length_preset: str
    languages: list[str]
    sources: list[SourceRef]


class OutlineChapter(TypedDict):
    slug: str
    title: str
    ordinal: int
    summary: str


class OutlineDraft(TypedDict):
    version: int
    title: str
    summary: str
    chapters: list[OutlineChapter]


class ChapterDraft(TypedDict):
    slug: str
    title: str
    ordinal: int
    status: str
    revision: int
    content_hash: str | None
    locked: bool


class DiagramDraft(TypedDict):
    chapter_slug: str
    kind: str
    status: str
    mermaid_hash: str | None


class ApprovalRecord(TypedDict):
    decision: str
    note: str
    decided_by: str | None
    outline_version: int


class ValidationReport(TypedDict):
    passed: bool
    findings: list[str]


class PublicationRecord(TypedDict):
    bundle_hash: str | None
    published_at: str | None


class NodeRecord(TypedDict):
    """Everything needed to explain, price and reproduce one execution of one node."""

    node: str
    phase: str
    pipeline_version: str
    input_hash: str
    prompt_hash: str | None
    model: str | None
    attempt: int
    tokens_in: int
    tokens_out: int
    cost_usd: str
    idempotency_key: str
    finished_at: str
    error_code: str | None
    error_message: str | None


class UsageTotals(TypedDict):
    tokens_in: int
    tokens_out: int
    cost_usd: str


def advance_phase_value(current: str | None, incoming: str | None) -> str | None:
    """Move the phase forward, refusing every backward step the run state machine does.

    The rule lives in ``app.runs.state_machine`` because MySQL enforces the same one; this
    reducer just applies it to the copy the graph carries. An empty current phase is the
    value the channel starts at before any node has reported one.
    """
    if not incoming:
        return current
    if not current:
        return incoming
    assert_phase_change(RunPhase(current), RunPhase(incoming))
    return incoming


def merge_chapters(
    current: dict[str, ChapterDraft] | None,
    incoming: dict[str, ChapterDraft] | None,
) -> dict[str, ChapterDraft]:
    """Apply new chapter drafts, leaving locked ones exactly as the reviewer left them.

    Regenerating part of a tutorial writes every chapter it produced; without this guard a
    single regeneration would silently throw away accepted text.
    """
    merged = dict(current or {})
    for slug, draft in (incoming or {}).items():
        existing = merged.get(slug)
        if existing is not None and existing.get("locked"):
            continue
        merged[slug] = draft
    return merged


def merge_diagrams(
    current: dict[str, DiagramDraft] | None,
    incoming: dict[str, DiagramDraft] | None,
) -> dict[str, DiagramDraft]:
    return {**(current or {}), **(incoming or {})}


def append_records(
    current: list[NodeRecord] | None, incoming: list[NodeRecord] | None
) -> list[NodeRecord]:
    return [*(current or []), *(incoming or [])]


def accumulate_usage(
    current: UsageTotals | None, incoming: UsageTotals | None
) -> UsageTotals:
    left = current or ZERO_USAGE
    right = incoming or ZERO_USAGE
    total = Decimal(left["cost_usd"]) + Decimal(right["cost_usd"])
    return {
        "tokens_in": left["tokens_in"] + right["tokens_in"],
        "tokens_out": left["tokens_out"] + right["tokens_out"],
        "cost_usd": _render_money(total),
    }


def merge_attempts(
    current: dict[str, int] | None, incoming: dict[str, int] | None
) -> dict[str, int]:
    """Keep the highest attempt seen per node so a replay cannot reset the counter."""
    merged = dict(current or {})
    for node, attempt in (incoming or {}).items():
        merged[node] = max(merged.get(node, 0), attempt)
    return merged


class TutorialState(TypedDict, total=False):
    """The graph state. Written by nodes, checkpointed between them."""

    run_id: str
    project_id: str
    thread_id: str
    pipeline_version: str
    phase: Annotated[str, advance_phase_value]
    request: TutorialRequest
    discovered: list[SourceRef]
    snapshots: list[str]
    corpus: dict[str, Any]
    index: dict[str, Any]
    analysis: dict[str, Any]
    outline: OutlineDraft
    approval: ApprovalRecord
    chapters: Annotated[dict[str, ChapterDraft], merge_chapters]
    diagrams: Annotated[dict[str, DiagramDraft], merge_diagrams]
    validation: ValidationReport
    publication: PublicationRecord
    records: Annotated[list[NodeRecord], append_records]
    attempts: Annotated[dict[str, int], merge_attempts]
    usage: Annotated[UsageTotals, accumulate_usage]


def input_digest(payload: Any) -> str:
    """Hash a node's inputs so an unchanged input can be recognised across runs."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_ref(kind: str, locator: str) -> SourceRef:
    normalised = locator.strip()
    return {
        "kind": SourceKind(kind).value,
        "locator": normalised,
        "fingerprint": hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
    }


def tutorial_request(
    *,
    title: str,
    output_language: str = "zh",
    reader_level: str = "beginner",
    length_preset: str = "standard",
    languages: list[str] | None = None,
    sources: list[dict[str, str]] | None = None,
) -> TutorialRequest:
    """Build the request. ``languages`` are the code languages analysed in depth."""
    return {
        "title": title,
        "output_language": output_language,
        "reader_level": ReaderLevel(reader_level).value,
        "length_preset": LengthPreset(length_preset).value,
        "languages": [CodeLanguage(language).value for language in languages or []],
        "sources": [source_ref(entry["kind"], entry["locator"]) for entry in sources or []],
    }


def chapter_draft(
    *,
    slug: str,
    title: str,
    ordinal: int,
    status: ChapterStatus = ChapterStatus.PENDING,
    revision: int = 1,
    content_hash: str | None = None,
    locked: bool = False,
) -> ChapterDraft:
    return {
        "slug": slug,
        "title": title,
        "ordinal": ordinal,
        "status": ChapterStatus(status).value,
        "revision": revision,
        "content_hash": content_hash,
        "locked": locked,
    }


def node_record(
    *,
    node: str,
    phase: RunPhase,
    input_hash: str,
    attempt: int,
    prompt_hash: str | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: Decimal = Decimal("0"),
    idempotency_key: str = "",
    error_code: str | None = None,
    error_message: str | None = None,
) -> NodeRecord:
    return {
        "node": node,
        "phase": RunPhase(phase).value,
        "pipeline_version": PIPELINE_VERSION,
        "input_hash": input_hash,
        "prompt_hash": prompt_hash,
        "model": model,
        "attempt": attempt,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": _render_money(cost_usd),
        "idempotency_key": idempotency_key,
        "finished_at": utcnow().isoformat(),
        "error_code": error_code,
        "error_message": error_message,
    }


def new_tutorial_state(
    *,
    run_id: str,
    project_id: str,
    thread_id: str,
    request: TutorialRequest,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "project_id": project_id,
        "thread_id": thread_id,
        "pipeline_version": PIPELINE_VERSION,
        "phase": RunPhase.QUEUED.value,
        "request": request,
        "discovered": [],
        "snapshots": [],
        "chapters": {},
        "diagrams": {},
        "records": [],
        "attempts": {},
        "usage": dict(ZERO_USAGE),
    }


def _render_money(amount: Decimal) -> str:
    """Render a cost without an exponent or trailing zeros, so totals compare by value."""
    normalised = Decimal(amount).normalize()
    if normalised == 0:
        return "0"
    return format(normalised, "f")
