"""The one tutorial document model.

Both outputs -- the Markdown bundle and the published web page -- render from this model.
Keeping a single representation is not tidiness: two parallel copies of the same tutorial
drift within a release, and once they disagree a reader cannot tell which copy the
citations belong to.

The citation rules live here as validation rather than as a later quality gate, so an
ungrounded document cannot be constructed in the first place:

* a claim about the subject must reference at least one citation, unless it is explicitly a
  teaching abstraction the tutorial invented for the reader;
* every referenced citation id must exist, so a model cannot cite ``[7]`` into thin air;
* every citation must sit inside the snapshots the reviewer approved, which is what makes a
  reference reproducible later;
* code nobody can trace back to a source has to be labelled illustrative.

Diagram structure is defined in :mod:`app.tutorial.mermaid_ir` and re-exported here, so
callers have a single import for the whole document shape.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.db.models.enums import (
    ChapterStatus,
    CitationKind,
    ClaimKind,
    ClaimStatus,
    CodeLanguage,
    LengthPreset,
    ReaderLevel,
)
from app.tutorial.mermaid_ir import (
    MAX_EDGES,
    MAX_LABEL_LENGTH,
    MAX_NODES,
    SLUG_PATTERN,
    DiagramDirection,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    EdgeKind,
    MermaidDiagram,
    NodeRole,
    Slug,
)

#: Bumped when the document shape changes in a way a stored artifact cannot be read with.
SCHEMA_VERSION: Final = "1"

#: Claim kinds that describe the subject and therefore need a source. A teaching
#: abstraction is the tutorial's own invention and is labelled as such instead.
GROUNDED_CLAIM_KINDS: Final = frozenset(
    {ClaimKind.FACT, ClaimKind.API_SIGNATURE, ClaimKind.BEHAVIOUR}
)

#: ``owner/repo@<40 hex>/path`` with an optional ``#Lx-Ly`` range.
_REPO_LOCATOR: Final = re.compile(r"\A[\w.-]+/[\w.-]+@[0-9a-f]{40}/\S+\Z")
_WEB_LOCATOR: Final = re.compile(r"\Ahttps?://\S+\Z")

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
ShortText = Annotated[str, StringConstraints(max_length=512)]

#: Document-local identifiers for citations, facts and exercises.
LOCAL_ID_PATTERN: Final = r"^[A-Za-z0-9_.:-]{1,32}$"
LocalId = Annotated[str, StringConstraints(pattern=LOCAL_ID_PATTERN)]


class _Frozen(BaseModel):
    """Every part of the document is immutable and refuses unknown fields.

    ``extra="forbid"`` is a content control as much as a typing one: a model that invents a
    field is trying to add content the renderers would never show and the quality gate
    would never check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceCitation(_Frozen):
    """One citable location inside an approved snapshot.

    ``id`` is document-local (``e1``, ``e2``) and is what blocks, facts and diagrams point
    at; the durable identity is ``snapshot_id`` plus ``locator``.
    """

    id: LocalId
    kind: CitationKind
    snapshot_id: NonEmptyText
    locator: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    title: ShortText = ""
    quote: Annotated[str, StringConstraints(max_length=2000)] = ""
    document_id: str | None = None
    chunk_id: str | None = None
    symbol_id: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    retrieved_at: datetime | None = None

    @model_validator(mode="after")
    def _locator_matches_the_kind(self) -> Self:
        if self.kind is CitationKind.WEB and not _WEB_LOCATOR.match(self.locator):
            raise ValueError(
                f"a web citation locator must be the fetched http(s) URL, got {self.locator!r}"
            )
        if self.kind is CitationKind.REPO and not _REPO_LOCATOR.match(self.locator):
            raise ValueError(
                "a repository citation must be pinned to a commit as "
                f"owner/repo@<commit sha>/path, got {self.locator!r}"
            )
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("end_line cannot precede start_line")
        return self


class _Cited(_Frozen):
    citation_ids: tuple[str, ...] = ()


class AtomicFact(_Cited):
    """One checkable statement, small enough for a reviewer to accept or reject alone."""

    id: LocalId
    statement: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    kind: ClaimKind = ClaimKind.FACT
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _grounded_unless_it_is_our_own_abstraction(self) -> Self:
        if not self.statement.strip():
            raise ValueError("a fact needs a statement")
        if self.kind in GROUNDED_CLAIM_KINDS and not self.citation_ids:
            raise ValueError(
                f"a {self.kind.value} claim needs at least one citation; mark it as a "
                f"{ClaimKind.TEACHING_ABSTRACTION.value} if it is the tutorial's own analogy"
            )
        return self


class GlossaryTerm(_Cited):
    term: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    definition: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    teaching_abstraction: bool = False


class Exercise(_Cited):
    """A task for the reader. Generated guidance, so evidence is optional but resolved."""

    id: LocalId
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    hints: tuple[Annotated[str, StringConstraints(max_length=500)], ...] = ()
    solution_markdown: str = ""


class MarkdownBlock(_Cited):
    """Prose. Citations are optional because section scaffolding claims nothing; the facts
    in the same chapter carry the evidence for what the prose says."""

    type: Literal["markdown"] = "markdown"
    markdown: NonEmptyText

    @model_validator(mode="after")
    def _not_blank(self) -> Self:
        if not self.markdown.strip():
            raise ValueError("a markdown block needs content")
        return self


class CodeBlock(_Cited):
    """A code sample, either taken from a source or admitted to be illustrative.

    ``verified`` is set by the quality gate after the sample compiles or type checks; the
    generator never claims it.
    """

    type: Literal["code"] = "code"
    language: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    code: NonEmptyText
    caption: ShortText = ""
    illustrative: bool = False
    verified: bool = False

    @model_validator(mode="after")
    def _traceable_or_labelled(self) -> Self:
        if not self.code.strip():
            raise ValueError("a code block needs code")
        if not self.citation_ids and not self.illustrative:
            raise ValueError(
                "cite the source this code came from, or set illustrative so the reader is "
                "told it is indicative rather than copied"
            )
        return self


class DiagramBlock(_Frozen):
    """A diagram plus the text a reader gets if rendering it ever fails."""

    type: Literal["diagram"] = "diagram"
    diagram: MermaidDiagram
    caption: ShortText = ""
    fallback_markdown: str = ""


Block = Annotated[MarkdownBlock | CodeBlock | DiagramBlock, Field(discriminator="type")]


class TutorialChapter(_Frozen):
    """One chapter: ordered blocks plus the facts, exercises and status around them."""

    slug: Slug
    ordinal: int = Field(ge=0)
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    summary: Annotated[str, StringConstraints(max_length=2000)] = ""
    blocks: Annotated[tuple[Block, ...], Field(min_length=1)]
    facts: tuple[AtomicFact, ...] = ()
    exercises: tuple[Exercise, ...] = ()
    status: ChapterStatus = ChapterStatus.DRAFTED
    revision: int = Field(default=1, ge=1)

    def citation_ids(self) -> frozenset[str]:
        referenced: set[str] = set()
        for block in self.blocks:
            if isinstance(block, DiagramBlock):
                referenced |= block.diagram.citation_ids()
            else:
                referenced |= set(block.citation_ids)
        for fact in self.facts:
            referenced |= set(fact.citation_ids)
        for exercise in self.exercises:
            referenced |= set(exercise.citation_ids)
        return frozenset(referenced)

    @model_validator(mode="after")
    def _unique_local_ids(self) -> Self:
        _reject_duplicates((fact.id for fact in self.facts), "fact id")
        _reject_duplicates((exercise.id for exercise in self.exercises), "exercise id")
        _reject_duplicates(
            (
                block.diagram.slug
                for block in self.blocks
                if isinstance(block, DiagramBlock)
            ),
            "diagram slug",
        )
        return self


class TutorialMetadata(_Frozen):
    """Everything about the tutorial that is not its content.

    ``snapshot_ids`` is the approved evidence scope. It is metadata rather than an argument
    to a validator so a stored document carries the scope it was checked against.
    """

    schema_version: str = SCHEMA_VERSION
    project_id: NonEmptyText
    run_id: NonEmptyText
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    slug: Slug
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    reader_level: ReaderLevel = ReaderLevel.INTERMEDIATE
    length_preset: LengthPreset = LengthPreset.STANDARD
    languages: tuple[CodeLanguage, ...] = ()
    snapshot_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    outline_version: int = Field(default=1, ge=1)
    pipeline_version: str = "1"
    generator_model: ShortText = ""
    generated_at: datetime | None = None


class TutorialDocument(_Frozen):
    """A whole tutorial: metadata, chapters, the citation ledger and the glossary."""

    metadata: TutorialMetadata
    chapters: Annotated[tuple[TutorialChapter, ...], Field(min_length=1)]
    citations: tuple[EvidenceCitation, ...] = ()
    glossary: tuple[GlossaryTerm, ...] = ()

    def citation_index(self) -> Mapping[str, EvidenceCitation]:
        return {citation.id: citation for citation in self.citations}

    def iter_blocks(self) -> Iterator[tuple[TutorialChapter, Any]]:
        for chapter in self.chapters:
            for block in chapter.blocks:
                yield chapter, block

    def iter_facts(self) -> Iterator[tuple[TutorialChapter, AtomicFact]]:
        for chapter in self.chapters:
            for fact in chapter.facts:
                yield chapter, fact

    def diagrams(self) -> tuple[MermaidDiagram, ...]:
        return tuple(
            block.diagram
            for _, block in self.iter_blocks()
            if isinstance(block, DiagramBlock)
        )

    def canonical_json(self) -> str:
        """Return a byte-stable serialisation of the document.

        Keys are sorted and separators fixed so the same content always produces the same
        bytes, whatever order it was assembled in. That is what makes the digest below a
        usable content address and what lets a re-run be recognised as a no-op.
        """
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def content_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def _internally_consistent(self) -> Self:
        _reject_duplicates((citation.id for citation in self.citations), "citation id")
        _reject_duplicates((chapter.slug for chapter in self.chapters), "chapter slug")

        for position, chapter in enumerate(self.chapters):
            if chapter.ordinal != position:
                raise ValueError(
                    f"chapter {chapter.slug!r} has ordinal {chapter.ordinal} but sits at "
                    f"position {position}; ordinals are the reading order"
                )

        approved = set(self.metadata.snapshot_ids)
        for citation in self.citations:
            if citation.snapshot_id not in approved:
                raise ValueError(
                    f"citation {citation.id!r} points at snapshot "
                    f"{citation.snapshot_id!r}, which is not in the approved snapshot scope"
                )

        known = {citation.id for citation in self.citations}
        referenced = frozenset(
            citation_id
            for chapter in self.chapters
            for citation_id in chapter.citation_ids()
        ) | frozenset(
            citation_id for term in self.glossary for citation_id in term.citation_ids
        )
        missing = sorted(referenced - known)
        if missing:
            raise ValueError(f"unknown citation ids referenced: {', '.join(missing)}")
        return self


def _reject_duplicates(values: Iterator[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label} {value!r}")
        seen.add(value)


__all__ = [
    "GROUNDED_CLAIM_KINDS",
    "MAX_EDGES",
    "MAX_LABEL_LENGTH",
    "MAX_NODES",
    "SCHEMA_VERSION",
    "SLUG_PATTERN",
    "AtomicFact",
    "Block",
    "CodeBlock",
    "DiagramBlock",
    "DiagramDirection",
    "DiagramEdge",
    "DiagramKind",
    "DiagramNode",
    "EdgeKind",
    "EvidenceCitation",
    "Exercise",
    "GlossaryTerm",
    "MarkdownBlock",
    "MermaidDiagram",
    "NodeRole",
    "TutorialChapter",
    "TutorialDocument",
    "TutorialMetadata",
]
