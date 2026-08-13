"""The ordered generation steps, as functions the workflow graph can call.

The sequence is corpus map, module summaries, evidence outline, human approval, per-chapter
evidence pack, chapter generation, global consistency revision. Each step is a plain function
taking an injected model and returning data: the graph owns orchestration, the interrupt and
persistence, and this module owns what each step means. Nothing here touches LangGraph, so
every step is testable on its own with a scripted model.

Two decisions are worth stating because they are what keeps the output honest.

Module summaries are navigation only. They are how the outline step knows what the corpus
contains, and they never reach a chapter as evidence: a chapter's facts must cite the
snapshot excerpts in its evidence pack, not a summary of them.

The consistency pass returns a bounded set of edits -- terminology, chapter summaries,
duplicate facts to drop, glossary entries -- rather than rewritten prose. A model handed a
finished document to rewrite drops citations and reintroduces unsupported statements; a model
asked for edits cannot, because the edit schema has no field that carries new claims.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal, Self

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from app.db.models import Document, Symbol
from app.db.models.enums import ApprovalDecision, CodeLanguage, DocumentKind
from app.llm.providers import ChatModel
from app.llm.structured import StructuredResult, generate_structured
from app.llm.usage import UsageLedger
from app.tutorial.citations import CitationAudit, CitationLedger, LedgerEntry
from app.tutorial.evidence import EvidencePack, EvidencePackBuilder, EvidenceRequest
from app.tutorial.mermaid_ir import Slug
from app.tutorial.prompts import (
    chapter_messages,
    consistency_messages,
    outline_messages,
    summary_messages,
)
from app.tutorial.schema import (
    GROUNDED_CLAIM_KINDS,
    AtomicFact,
    Block,
    CodeBlock,
    EvidenceCitation,
    Exercise,
    GlossaryTerm,
    MarkdownBlock,
    TutorialChapter,
    TutorialDocument,
    TutorialMetadata,
)

#: The generation order, for logging and for the graph to keep in step with this module.
GENERATION_STEPS: Final[tuple[str, ...]] = (
    "corpus_map",
    "module_summaries",
    "evidence_outline",
    "outline_approval",
    "chapter_evidence",
    "chapter_generation",
    "consistency_revision",
)

DEFAULT_MAX_GROUPS: Final = 20
_SAMPLES_PER_GROUP: Final = 3
_SYMBOLS_PER_GROUP: Final = 5
_MAX_CHAPTERS: Final = 24
_ROOT_GROUP: Final = "(root)"

ShortLine = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class EvidenceMissing(Exception):
    """Raised when a chapter has no evidence, so writing it could only be invention."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class CorpusGroup:
    """One coherent part of the corpus: a directory of code or a section of a site."""

    key: str
    title: str
    document_count: int
    languages: tuple[CodeLanguage, ...] = ()
    sample_locators: tuple[str, ...] = ()
    top_symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusMap:
    """What was collected, grouped, counted and deterministic.

    Computed from the database rather than from a model: the shape of a corpus is a fact,
    and paying a model to restate it would make the outline depend on a sampled summary of
    something already known exactly.
    """

    project_id: str
    snapshot_ids: tuple[str, ...]
    document_count: int
    symbol_count: int
    groups: tuple[CorpusGroup, ...] = ()
    truncated: bool = False

    def overview(self) -> str:
        """Render the map as stable text for a prompt."""
        if not self.groups:
            return "(the approved snapshots contain no indexed documents)"
        lines = [
            f"{self.document_count} documents, {self.symbol_count} code symbols, "
            f"grouped into {len(self.groups)} areas"
            + (" (truncated)" if self.truncated else "")
        ]
        for group in self.groups:
            lines.append(f"- {group.key}: {group.document_count} documents")
            if group.languages:
                lines.append(
                    "  languages: " + ", ".join(language.value for language in group.languages)
                )
            if group.top_symbols:
                lines.append("  symbols: " + ", ".join(group.top_symbols))
            if group.sample_locators:
                lines.append("  examples: " + ", ".join(group.sample_locators))
        return "\n".join(lines)


def build_corpus_map(
    session: Session,
    *,
    project_id: str,
    snapshot_ids: Sequence[str],
    max_groups: int = DEFAULT_MAX_GROUPS,
) -> CorpusMap:
    """Group the approved corpus into the areas an outline can be built from."""
    documents = session.execute(
        sa.select(
            Document.id,
            Document.kind,
            Document.path,
            Document.uri,
            Document.title,
            Document.code_language,
        )
        .where(Document.project_id == project_id, *_snapshot_filter(snapshot_ids))
        .order_by(Document.path, Document.uri, Document.id)
    ).all()
    symbols = session.execute(
        sa.select(Symbol.document_id, Symbol.qualified_name)
        .join(Document, Symbol.document_id == Document.id)
        .where(Symbol.project_id == project_id, *_snapshot_filter(snapshot_ids))
        .order_by(Symbol.qualified_name, Symbol.id)
    ).all()

    symbols_by_document: dict[str, list[str]] = {}
    for row in symbols:
        symbols_by_document.setdefault(row.document_id, []).append(row.qualified_name)

    grouped: dict[str, dict[str, Any]] = {}
    for row in documents:
        key = _group_key(row.kind, row.path, row.uri)
        group = grouped.setdefault(
            key, {"count": 0, "languages": [], "samples": [], "symbols": []}
        )
        group["count"] += 1
        if row.code_language and row.code_language not in group["languages"]:
            group["languages"].append(row.code_language)
        if len(group["samples"]) < _SAMPLES_PER_GROUP:
            group["samples"].append(row.path or row.uri)
        for name in symbols_by_document.get(row.id, ()):
            if len(group["symbols"]) < _SYMBOLS_PER_GROUP and name not in group["symbols"]:
                group["symbols"].append(name)

    ordered = sorted(grouped.items(), key=lambda item: (-item[1]["count"], item[0]))
    groups = tuple(
        CorpusGroup(
            key=key,
            title=key,
            document_count=data["count"],
            languages=tuple(data["languages"]),
            sample_locators=tuple(data["samples"]),
            top_symbols=tuple(data["symbols"]),
        )
        for key, data in ordered[:max_groups]
    )
    return CorpusMap(
        project_id=project_id,
        snapshot_ids=tuple(snapshot_ids),
        document_count=len(documents),
        symbol_count=len(symbols),
        groups=groups,
        truncated=len(ordered) > len(groups),
    )


class ModuleSummary(_Model):
    """A short description of one corpus area, for orientation only.

    ``navigation_only`` is a literal rather than a flag a model can clear: the type itself
    says this text is not a source, and a chapter that wants to state something has to cite
    the snapshot instead.
    """

    group_key: ShortLine
    title: ShortLine
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    related_paths: tuple[ShortLine, ...] = ()
    navigation_only: Literal[True] = True


class ModuleSummaries(_Model):
    summaries: Annotated[tuple[ModuleSummary, ...], Field(min_length=1, max_length=60)]

    def notes(self) -> tuple[str, ...]:
        return tuple(f"{summary.title}: {summary.purpose}" for summary in self.summaries)


def summarise_modules(
    model: ChatModel,
    corpus: CorpusMap,
    *,
    ledger: UsageLedger | None = None,
) -> StructuredResult[ModuleSummaries]:
    """Describe each corpus area so the outline step can navigate it."""
    return generate_structured(
        model,
        schema=ModuleSummaries,
        messages=summary_messages(corpus_overview=corpus.overview()),
        ledger=ledger,
    )


class OutlineChapterProposal(_Model):
    """One proposed chapter, including what evidence it will need.

    The evidence hints are the point of proposing chapters before writing them: they are
    what lets an evidence pack be assembled per chapter after a human approves the shape.
    """

    slug: Slug
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    intent: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    questions: Annotated[tuple[ShortLine, ...], Field(min_length=1, max_length=10)]
    path_hints: tuple[ShortLine, ...] = ()
    symbols: tuple[ShortLine, ...] = ()


class OutlineProposal(_Model):
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    summary: Annotated[str, StringConstraints(max_length=2000)] = ""
    chapters: Annotated[
        tuple[OutlineChapterProposal, ...], Field(min_length=1, max_length=_MAX_CHAPTERS)
    ]

    @model_validator(mode="after")
    def _unique_slugs(self) -> Self:
        seen: set[str] = set()
        for chapter in self.chapters:
            if chapter.slug in seen:
                raise ValueError(f"duplicate chapter slug {chapter.slug!r}")
            seen.add(chapter.slug)
        return self


def draft_evidence_outline(
    model: ChatModel,
    corpus: CorpusMap,
    *,
    tutorial_title: str,
    reader_level: str,
    length_preset: str,
    summaries: ModuleSummaries | None = None,
    ledger: UsageLedger | None = None,
) -> StructuredResult[OutlineProposal]:
    """Propose the table of contents a reviewer will approve, reject or edit."""
    messages = outline_messages(
        tutorial_title=tutorial_title,
        reader_level=reader_level,
        length_preset=length_preset,
        corpus_overview=corpus.overview(),
        navigation_notes=summaries.notes() if summaries else (),
    )
    return generate_structured(
        model,
        schema=OutlineProposal,
        messages=messages,
        ledger=ledger,
    )


@dataclass(frozen=True, slots=True)
class ApprovedOutline:
    """The outline chapters are written from, as approved."""

    title: str
    summary: str
    chapters: tuple[OutlineChapterProposal, ...]
    version: int = 1


@dataclass(frozen=True, slots=True)
class OutlineDecision:
    """A human decision on a proposed outline."""

    decision: ApprovalDecision
    outline: ApprovedOutline | None = None
    note: str = ""

    @property
    def approved(self) -> bool:
        return self.decision is ApprovalDecision.APPROVED and self.outline is not None


def outline_approval_payload(proposal: OutlineProposal) -> dict[str, Any]:
    """Render the proposal as the plain data the approval interrupt shows a reviewer."""
    return {
        "title": proposal.title,
        "summary": proposal.summary,
        "chapters": [
            {
                "slug": chapter.slug,
                "title": chapter.title,
                "intent": chapter.intent,
                "questions": list(chapter.questions),
                "path_hints": list(chapter.path_hints),
                "symbols": list(chapter.symbols),
            }
            for chapter in proposal.chapters
        ],
    }


def apply_outline_decision(
    proposal: OutlineProposal,
    *,
    decision: ApprovalDecision,
    chapter_slugs: Sequence[str] | None = None,
    titles: Mapping[str, str] | None = None,
    note: str = "",
    version: int = 1,
) -> OutlineDecision:
    """Turn a reviewer's decision into the outline the chapter steps will use.

    Pure on purpose: the interrupt itself belongs to the graph, and this function is what the
    graph calls on both sides of it. Editing is limited to choosing chapters, ordering them
    and retitling them, so an approval cannot introduce a chapter nobody proposed evidence
    for.
    """
    if decision is ApprovalDecision.PENDING:
        raise ValueError("a pending approval is not a decision")
    if decision is not ApprovalDecision.APPROVED:
        return OutlineDecision(decision=decision, note=note)

    by_slug = {chapter.slug: chapter for chapter in proposal.chapters}
    order = tuple(chapter_slugs) if chapter_slugs is not None else tuple(by_slug)
    unknown = [slug for slug in order if slug not in by_slug]
    if unknown:
        raise ValueError(f"unknown chapter slugs in the approval: {', '.join(unknown)}")
    if not order:
        raise ValueError("an approved outline needs at least one chapter")

    renames = dict(titles or {})
    chapters = tuple(
        by_slug[slug].model_copy(update={"title": renames[slug]})
        if slug in renames
        else by_slug[slug]
        for slug in order
    )
    return OutlineDecision(
        decision=decision,
        outline=ApprovedOutline(
            title=proposal.title,
            summary=proposal.summary,
            chapters=chapters,
            version=version,
        ),
        note=note,
    )


def build_chapter_evidence(
    builder: EvidencePackBuilder,
    chapter: OutlineChapterProposal,
    *,
    project_id: str,
    snapshot_ids: Sequence[str],
    max_items: int | None = None,
    char_budget: int | None = None,
) -> EvidencePack:
    """Assemble the evidence pack for one approved chapter."""
    overrides: dict[str, Any] = {}
    if max_items is not None:
        overrides["max_items"] = max_items
    if char_budget is not None:
        overrides["char_budget"] = char_budget
    return builder.build(
        EvidenceRequest(
            project_id=project_id,
            chapter_slug=chapter.slug,
            chapter_title=chapter.title,
            snapshot_ids=tuple(snapshot_ids),
            questions=chapter.questions,
            symbols=chapter.symbols,
            path_hints=chapter.path_hints,
            **overrides,
        )
    )


DraftBlock = Annotated[MarkdownBlock | CodeBlock, Field(discriminator="type")]


class ChapterDraft(_Model):
    """What a model returns for one chapter.

    Diagrams are absent by design: they are generated in a later phase from the diagram IR,
    and asking for them here would mean a chapter's prose and its diagram were validated
    against different evidence.
    """

    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    summary: Annotated[str, StringConstraints(max_length=2000)] = ""
    blocks: Annotated[tuple[DraftBlock, ...], Field(min_length=1, max_length=40)]
    facts: tuple[AtomicFact, ...] = ()
    exercises: tuple[Exercise, ...] = ()


@dataclass(frozen=True, slots=True)
class ChapterGeneration:
    """A generated chapter, the citations it kept, and what was rejected on the way."""

    chapter: TutorialChapter
    citations: tuple[EvidenceCitation, ...]
    audit: CitationAudit
    result: StructuredResult[ChapterDraft]
    rejected: tuple[LedgerEntry, ...] = ()


def generate_chapter(
    model: ChatModel,
    *,
    plan: OutlineChapterProposal,
    pack: EvidencePack,
    ordinal: int,
    reader_level: str = "intermediate",
    length_hint: str = "",
    navigation_notes: Sequence[str] = (),
    ledger: UsageLedger | None = None,
) -> ChapterGeneration:
    """Write one chapter from its evidence pack, keeping only what the evidence supports.

    A reference to evidence that was not retrieved is not repaired by re-prompting: it is
    removed. A fact left without a source is dropped and recorded as rejected, and a code
    sample left without a source is relabelled illustrative rather than presented as copied
    from the project.
    """
    if pack.is_empty:
        raise EvidenceMissing(
            f"chapter {plan.slug!r} has no evidence; nothing can be written from it"
        )

    result = generate_structured(
        model,
        schema=ChapterDraft,
        messages=chapter_messages(
            chapter_title=plan.title,
            chapter_intent=plan.intent,
            pack=pack,
            reader_level=reader_level,
            length_hint=length_hint,
            navigation_notes=navigation_notes,
        ),
        ledger=ledger,
    )

    allowed = frozenset(pack.citation_ids())
    blocks = tuple(_prune_block(block, allowed) for block in result.value.blocks)
    facts, dropped_facts = _prune_facts(result.value.facts, allowed)
    exercises = tuple(
        _revalidate(exercise, citation_ids=_kept(exercise.citation_ids, allowed))
        for exercise in result.value.exercises
    )

    chapter = TutorialChapter(
        slug=plan.slug,
        ordinal=ordinal,
        title=result.value.title,
        summary=result.value.summary,
        blocks=blocks,
        facts=facts,
        exercises=exercises,
    )
    used = chapter.citation_ids()
    citations = tuple(
        citation for citation in pack.to_citations() if citation.id in used
    )

    # The chapter is audited on its own, so it is placed first in a throwaway document; its
    # position in the finished tutorial is decided later, when the chapters are assembled.
    audit = CitationLedger({plan.slug: pack}).audit_document(
        TutorialDocument(
            metadata=_audit_metadata(pack),
            chapters=(_revalidate(chapter, ordinal=0),),
            citations=citations,
        )
    )
    rejected = (*dropped_facts, *audit.rejected)
    return ChapterGeneration(
        chapter=chapter,
        citations=citations,
        audit=audit,
        result=result,
        rejected=rejected,
    )


def assemble_document(
    metadata: TutorialMetadata,
    generations: Sequence[ChapterGeneration],
    *,
    glossary: Sequence[GlossaryTerm] = (),
) -> TutorialDocument:
    """Merge generated chapters into one document with a single citation ledger.

    Each pack numbers its evidence from ``e1``, so two chapters arrive with colliding ids
    that mean different sources. Ids are renumbered document-wide and every reference is
    rewritten; the same source cited by two chapters collapses to one citation.
    """
    citations: list[EvidenceCitation] = []
    by_source: dict[tuple[str, str], str] = {}
    chapters: list[TutorialChapter] = []

    for ordinal, generation in enumerate(generations):
        mapping: dict[str, str] = {}
        for citation in generation.citations:
            source = (citation.snapshot_id, citation.locator)
            existing = by_source.get(source)
            if existing is None:
                existing = f"c{len(citations) + 1}"
                by_source[source] = existing
                citations.append(_revalidate(citation, id=existing))
            mapping[citation.id] = existing
        chapters.append(_remap_chapter(generation.chapter, mapping, ordinal=ordinal))

    return TutorialDocument(
        metadata=metadata,
        chapters=tuple(chapters),
        citations=tuple(citations),
        glossary=tuple(glossary),
    )


class TerminologyFix(_Model):
    term: ShortLine
    canonical: ShortLine


class ChapterSummaryFix(_Model):
    slug: Slug
    summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class ConsistencyRevision(_Model):
    """The bounded set of edits a global consistency pass may make.

    There is deliberately no field that can carry new prose, new code or new facts: the pass
    exists to make a document internally consistent, and anything it could add would arrive
    without evidence attached.
    """

    terminology: tuple[TerminologyFix, ...] = ()
    chapter_summaries: tuple[ChapterSummaryFix, ...] = ()
    drop_fact_ids: tuple[ShortLine, ...] = ()
    glossary: tuple[GlossaryTerm, ...] = ()
    notes: Annotated[str, StringConstraints(max_length=2000)] = ""


@dataclass(frozen=True, slots=True)
class DocumentRevision:
    """The revised document, the edits that produced it and the edits that were refused."""

    document: TutorialDocument
    revision: ConsistencyRevision
    result: StructuredResult[ConsistencyRevision]
    dropped: tuple[str, ...] = ()


def revise_for_consistency(
    model: ChatModel,
    document: TutorialDocument,
    *,
    ledger: UsageLedger | None = None,
) -> DocumentRevision:
    """Unify terminology and remove duplication across the whole document."""
    outline = "\n".join(
        f"{chapter.ordinal + 1}. {chapter.title} ({chapter.slug}): {chapter.summary}"
        for chapter in document.chapters
    )
    facts = "\n".join(
        f"- {fact.id} [{chapter.slug}] {fact.statement}"
        for chapter, fact in document.iter_facts()
    )
    result = generate_structured(
        model,
        schema=ConsistencyRevision,
        messages=consistency_messages(
            outline=outline,
            facts=facts,
            evidence_ids=[citation.id for citation in document.citations],
        ),
        ledger=ledger,
    )
    revised, dropped = apply_revision(document, result.value)
    return DocumentRevision(
        document=revised,
        revision=result.value,
        result=result,
        dropped=dropped,
    )


def apply_revision(
    document: TutorialDocument,
    revision: ConsistencyRevision,
) -> tuple[TutorialDocument, tuple[str, ...]]:
    """Apply a revision deterministically, refusing the parts that are not supportable."""
    dropped: list[str] = []
    summaries = {fix.slug: fix.summary for fix in revision.chapter_summaries}
    drop_ids = frozenset(revision.drop_fact_ids)
    replacements = tuple((fix.term, fix.canonical) for fix in revision.terminology)

    chapters = tuple(
        _revalidate(
            chapter,
            summary=_rewrite(summaries.get(chapter.slug, chapter.summary), replacements),
            blocks=tuple(_rewrite_block(block, replacements) for block in chapter.blocks),
            facts=tuple(fact for fact in chapter.facts if fact.id not in drop_ids),
        )
        for chapter in document.chapters
    )

    known = {citation.id for citation in document.citations}
    glossary: list[GlossaryTerm] = list(document.glossary)
    for term in revision.glossary:
        unknown = [citation_id for citation_id in term.citation_ids if citation_id not in known]
        if unknown:
            dropped.append(
                f"glossary term {term.term!r} cited unknown evidence: {', '.join(unknown)}"
            )
            continue
        glossary.append(term)

    revised = TutorialDocument(
        metadata=document.metadata,
        chapters=chapters,
        citations=document.citations,
        glossary=tuple(glossary),
    )
    return revised, tuple(dropped)


def _prune_block(block: Block, allowed: frozenset[str]) -> Block:
    """Remove references the pack does not contain, and relabel code that loses its source."""
    kept = _kept(block.citation_ids, allowed)
    if isinstance(block, CodeBlock):
        return _revalidate(block, citation_ids=kept, illustrative=block.illustrative or not kept)
    return _revalidate(block, citation_ids=kept)


def _prune_facts(
    facts: Sequence[AtomicFact],
    allowed: frozenset[str],
) -> tuple[tuple[AtomicFact, ...], tuple[LedgerEntry, ...]]:
    """Keep the facts the pack can back up, and record the ones it cannot."""
    kept: list[AtomicFact] = []
    rejected = CitationLedger()
    for fact in facts:
        surviving = _kept(fact.citation_ids, allowed)
        if surviving or fact.kind not in GROUNDED_CLAIM_KINDS:
            kept.append(_revalidate(fact, citation_ids=surviving))
            continue
        rejected.record(
            claim_id=fact.id,
            chapter_slug="",
            statement=fact.statement,
            kind=fact.kind,
            citations=(),
            missing_citation_ids=tuple(
                citation_id for citation_id in fact.citation_ids if citation_id not in allowed
            ),
        )
    return tuple(kept), rejected.entries


def _kept(citation_ids: Sequence[str], allowed: frozenset[str]) -> tuple[str, ...]:
    return tuple(citation_id for citation_id in citation_ids if citation_id in allowed)


def _remap_chapter(
    chapter: TutorialChapter,
    mapping: Mapping[str, str],
    *,
    ordinal: int,
) -> TutorialChapter:
    def remap(citation_ids: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            mapping[citation_id] for citation_id in citation_ids if citation_id in mapping
        )

    def remap_block(block: Block) -> Block:
        kept = remap(block.citation_ids)
        if isinstance(block, CodeBlock):
            return _revalidate(
                block, citation_ids=kept, illustrative=block.illustrative or not kept
            )
        return _revalidate(block, citation_ids=kept)

    return _revalidate(
        chapter,
        ordinal=ordinal,
        blocks=tuple(remap_block(block) for block in chapter.blocks),
        facts=tuple(
            _revalidate(fact, citation_ids=remap(fact.citation_ids)) for fact in chapter.facts
        ),
        exercises=tuple(
            _revalidate(exercise, citation_ids=remap(exercise.citation_ids))
            for exercise in chapter.exercises
        ),
    )


def _revalidate[T: BaseModel](model: T, **updates: Any) -> T:
    """Return a modified copy with every validator re-run.

    ``model_copy`` skips validation, which is the wrong behaviour for models whose whole
    point is an invariant: a code block that loses its citations has to end up labelled
    illustrative, not merely mutated.
    """
    return type(model).model_validate({**model.model_dump(), **updates})


def _rewrite(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    for term, canonical in replacements:
        if term:
            text = re.sub(rf"\b{re.escape(term)}\b", canonical, text)
    return text


def _rewrite_block(block: Block, replacements: Sequence[tuple[str, str]]) -> Block:
    """Rewrite prose only. A code sample must keep matching the source it cites."""
    if isinstance(block, MarkdownBlock):
        return block.model_copy(update={"markdown": _rewrite(block.markdown, replacements)})
    return block


def _audit_metadata(pack: EvidencePack) -> TutorialMetadata:
    """Minimal metadata so one chapter can be audited before the document exists."""
    return TutorialMetadata(
        project_id="audit",
        run_id="audit",
        title="audit",
        slug="audit",
        snapshot_ids=tuple(dict.fromkeys(item.snapshot_id for item in pack.items)),
    )


def _snapshot_filter(snapshot_ids: Sequence[str]) -> list[Any]:
    return [Document.snapshot_id.in_(tuple(snapshot_ids))] if snapshot_ids else []


def _group_key(kind: DocumentKind, path: str, uri: str) -> str:
    """Group a document by the area it belongs to: its directory, or a site section."""
    if kind is DocumentKind.REPO_FILE:
        directory = [segment for segment in (path or "").split("/")[:-1] if segment]
        return "/".join(directory[:2]) if directory else _ROOT_GROUP
    reference = path or uri
    segments = [segment for segment in reference.split("/") if segment and "://" not in segment]
    return segments[0] if segments else _ROOT_GROUP


__all__ = [
    "GENERATION_STEPS",
    "ApprovedOutline",
    "ChapterDraft",
    "ChapterGeneration",
    "ChapterSummaryFix",
    "ConsistencyRevision",
    "CorpusGroup",
    "CorpusMap",
    "DocumentRevision",
    "EvidenceMissing",
    "ModuleSummaries",
    "ModuleSummary",
    "OutlineChapterProposal",
    "OutlineDecision",
    "OutlineProposal",
    "TerminologyFix",
    "apply_outline_decision",
    "apply_revision",
    "assemble_document",
    "build_chapter_evidence",
    "build_corpus_map",
    "draft_evidence_outline",
    "generate_chapter",
    "outline_approval_payload",
    "revise_for_consistency",
    "summarise_modules",
]
