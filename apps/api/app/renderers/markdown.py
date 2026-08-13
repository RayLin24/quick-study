"""Render a tutorial document to a Markdown bundle.

The bundle is the offline deliverable: one ``tutorial.md``, one SVG per diagram and a
``sources.json`` mapping every citation id to its locator. It is written from the same
model the web page uses, so a downloaded copy cannot disagree with what was reviewed.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

from app.diagrams import render_diagram
from app.tutorial.schema import (
    CodeBlock,
    DiagramBlock,
    MarkdownBlock,
    TutorialDocument,
)


@dataclass(frozen=True, slots=True)
class MarkdownBundle:
    """The rendered files, ready to be stored or streamed."""

    markdown: str
    diagrams: dict[str, str]
    sources: dict[str, str]

    def to_zip_bytes(self) -> bytes:
        """Return the bundle as a ZIP archive."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("tutorial.md", self.markdown)
            for slug, svg in self.diagrams.items():
                archive.writestr(f"assets/diagrams/{slug}.svg", svg)
            archive.writestr("sources.json", json.dumps(self.sources, indent=2))
        return buffer.getvalue()


def render_markdown_bundle(document: TutorialDocument) -> MarkdownBundle:
    """Render ``document`` to Markdown, diagrams and a citation map."""
    diagrams: dict[str, str] = {}
    parts: list[str] = [
        f"# {document.metadata.title}",
        "",
        document.metadata.description,
        "",
    ]
    for chapter in document.chapters:
        parts.extend((f"## {chapter.title}", ""))
        if chapter.summary:
            parts.extend((chapter.summary, ""))
        for block in chapter.blocks:
            if isinstance(block, MarkdownBlock):
                parts.extend((block.markdown, ""))
            elif isinstance(block, CodeBlock):
                parts.extend((f"```{block.language}", block.code, "```", ""))
            elif isinstance(block, DiagramBlock):
                rendered = render_diagram(block.diagram)
                if rendered.ok and rendered.svg:
                    diagrams[block.diagram.slug] = rendered.svg
                    parts.extend(
                        (
                            f"![{block.diagram.title}](assets/diagrams/{block.diagram.slug}.svg)",
                            "",
                        )
                    )
                else:
                    parts.extend((block.fallback_markdown or block.diagram.title, ""))
        for exercise in chapter.exercises:
            parts.extend((f"### Exercise: {exercise.prompt}", ""))
            if exercise.solution_markdown:
                parts.extend((exercise.solution_markdown, ""))
    sources = {
        citation.id: citation.locator for citation in document.citations
    }
    return MarkdownBundle(
        markdown="\n".join(parts).strip() + "\n",
        diagrams=diagrams,
        sources=sources,
    )
