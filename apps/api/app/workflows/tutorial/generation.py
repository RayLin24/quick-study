"""Tutorial graph nodes that call the configured chat model.

Ingestion is still a stub, so these nodes treat registered source locators as the corpus
map and write chapter markdown from the approved outline. Snapshot-backed evidence packs
replace this once the crawler is wired in.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models.enums import ChapterStatus
from app.llm.errors import ModelCredentialsMissing
from app.llm.factory import build_chat_model
from app.llm.providers import system_message, user_message
from app.llm.providers.base import ChatModel
from app.llm.structured import generate_structured
from app.tutorial.pipeline import CorpusGroup, CorpusMap, OutlineProposal, draft_evidence_outline
from app.tutorial.prompts import SYSTEM_INSTRUCTIONS
from app.workflows.tutorial.nodes import TutorialNodes
from app.workflows.tutorial.recording import NodeCall, NodeOutcome
from app.workflows.tutorial.state import TutorialState, chapter_draft, input_digest

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
ShortLine = Annotated[str, StringConstraints(min_length=1, max_length=400)]


class ChapterMarkdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: ShortLine
    summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    markdown: ShortText


def corpus_from_state(state: TutorialState) -> CorpusMap:
    request = state["request"]
    sources = list(request.get("sources") or [])
    groups = tuple(
        CorpusGroup(
            key=str(source.get("locator") or source.get("fingerprint") or index),
            title=str(source.get("locator") or f"source-{index}"),
            document_count=0,
            sample_locators=(str(source.get("locator")),) if source.get("locator") else (),
        )
        for index, source in enumerate(sources)
    )
    return CorpusMap(
        project_id=state["project_id"],
        snapshot_ids=tuple(state.get("snapshots") or ()),
        document_count=0,
        symbol_count=0,
        groups=groups,
    )


def llm_draft_outline(model: ChatModel):
    def draft_outline(state: TutorialState, _call: NodeCall) -> NodeOutcome:
        request = state["request"]
        previous = state.get("outline")
        version = (previous["version"] + 1) if previous else 1
        result = draft_evidence_outline(
            model,
            corpus_from_state(state),
            tutorial_title=request["title"],
            reader_level=request["reader_level"],
            length_preset=request["length_preset"],
        )
        proposal: OutlineProposal = result.value
        chapters = [
            {
                "slug": chapter.slug,
                "title": chapter.title,
                "ordinal": ordinal,
                "summary": chapter.intent,
            }
            for ordinal, chapter in enumerate(proposal.chapters)
        ]
        return NodeOutcome(
            update={
                "outline": {
                    "version": version,
                    "title": proposal.title,
                    "summary": proposal.summary,
                    "chapters": chapters,
                }
            },
            model=result.model,
            prompt_hash=result.prompt_hash,
            tokens_in=result.usage.input_tokens,
            tokens_out=result.usage.output_tokens,
            cost_usd=result.cost_usd,
        )

    return draft_outline


def llm_write_chapters(model: ChatModel):
    def write_chapters(state: TutorialState, _call: NodeCall) -> NodeOutcome:
        outline = state["outline"]
        request = state["request"]
        locators = [
            str(source.get("locator"))
            for source in request.get("sources") or []
            if source.get("locator")
        ]
        drafts = {}
        tokens_in = 0
        tokens_out = 0
        cost = Decimal("0")
        prompt_hash = None
        model_name = model.spec.name
        for chapter in outline["chapters"]:
            source_lines = [f"- {locator}" for locator in locators] or [
                "- (no sources were registered)"
            ]
            result = generate_structured(
                model,
                schema=ChapterMarkdown,
                messages=(
                    system_message(SYSTEM_INSTRUCTIONS),
                    user_message(
                        "\n".join(
                            [
                                f"Write the chapter titled: {chapter['title']}",
                                f"What this chapter must achieve: {chapter.get('summary') or ''}",
                                f"Tutorial: {request['title']}",
                                f"Reader level: {request['reader_level']}",
                                "Registered sources (not snapshot evidence; say so when you "
                                "cannot verify a fact):",
                                *source_lines,
                                "Reply with markdown in the markdown field.",
                            ]
                        )
                    ),
                ),
            )
            drafts[chapter["slug"]] = chapter_draft(
                slug=chapter["slug"],
                title=result.value.title or chapter["title"],
                ordinal=chapter["ordinal"],
                status=ChapterStatus.DRAFTED,
                content_hash=input_digest(result.value.markdown),
                summary=result.value.summary,
                markdown=result.value.markdown,
            )
            tokens_in += result.usage.input_tokens
            tokens_out += result.usage.output_tokens
            cost += result.cost_usd
            prompt_hash = result.prompt_hash
            model_name = result.model
        return NodeOutcome(
            update={"chapters": drafts},
            model=model_name,
            prompt_hash=prompt_hash,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )

    return write_chapters


def build_tutorial_nodes(model: ChatModel | None = None) -> TutorialNodes:
    """Use the real model when credentials exist; otherwise keep the deterministic stubs."""
    chat = model
    if chat is None:
        try:
            chat = build_chat_model()
        except ModelCredentialsMissing:
            return TutorialNodes()
    return TutorialNodes(outline=llm_draft_outline(chat), chapters=llm_write_chapters(chat))
