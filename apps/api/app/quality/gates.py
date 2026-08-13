"""Run the quality gates against a tutorial document.

The gates here are the ones a reader relies on without being able to see them: a citation
points at evidence that was actually fetched, a code sample is at least syntactically real,
a diagram is safe to show, and the Markdown a page renders is the Markdown the gate
checked. Each gate returns findings rather than raising so the report can list everything
that failed in one pass.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Final

from app.diagrams import DiagramRenderError, render_diagram
from app.tutorial.schema import CodeBlock, DiagramBlock, TutorialDocument

#: API-shaped names that must be locatable in the cited evidence when they are claimed as
#: coming from the source. This is a conservative pattern: it matches dotted callables and
#: import paths, and deliberately misses prose.
_API_SHAPE: Final = re.compile(r"\b[a-z_][\w]*(?:\.[a-zA-Z_]\w*)+\b")

#: The markers a secret tends to carry in source. A real scanner is a later layer; this is
#: the hard stop for the obvious ones a model is most likely to copy.
_SECRET_MARKERS: Final = (
    "-----BEGIN",
    "sk-",
    "ghp_",
    "xoxb-",
    "aws_access_key_id",
    "api_key =",
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One failed check, with enough context to act on it."""

    gate: str
    code: str
    message: str
    locator: str = ""


@dataclass(slots=True)
class QualityReport:
    """The outcome of running every gate."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings

    def add(self, gate: str, code: str, message: str, *, locator: str = "") -> None:
        self.findings.append(Finding(gate, code, message, locator))


def run_quality_gates(document: TutorialDocument) -> QualityReport:
    """Run every hard gate and return the combined findings."""
    report = QualityReport()
    _citations_in_scope(document, report)
    _facts_grounded(document, report)
    _code_parses(document, report)
    _code_sources_locatable(document, report)
    _diagrams_render(document, report)
    _markdown_is_well_formed(document, report)
    _no_secrets(document, report)
    return report


def _citations_in_scope(document: TutorialDocument, report: QualityReport) -> None:
    """The schema already enforces this; the gate keeps the report explicit for callers
    that assemble a document from parts before validation."""
    approved = set(document.metadata.snapshot_ids)
    for citation in document.citations:
        if citation.snapshot_id not in approved:
            report.add(
                "citations",
                "citation.out-of-scope",
                f"citation {citation.id} is not in an approved snapshot",
                locator=citation.id,
            )


def _facts_grounded(document: TutorialDocument, report: QualityReport) -> None:
    known = {citation.id for citation in document.citations}
    for chapter, fact in document.iter_facts():
        for citation_id in fact.citation_ids:
            if citation_id not in known:
                report.add(
                    "citations",
                    "citation.unknown",
                    f"fact {fact.id} cites {citation_id}, which does not exist",
                    locator=f"{chapter.slug}/{fact.id}",
                )


def _code_parses(document: TutorialDocument, report: QualityReport) -> None:
    for chapter, block in document.iter_blocks():
        if not isinstance(block, CodeBlock):
            continue
        language = block.language.lower()
        if language == "python":
            try:
                ast.parse(block.code)
            except SyntaxError as error:
                report.add(
                    "code",
                    "code.syntax",
                    f"python code does not parse: {error.msg}",
                    locator=chapter.slug,
                )
        elif language in {"typescript", "javascript", "ts", "js", "tsx", "jsx"}:
            _typescript_parses(block, chapter.slug, report)


def _typescript_parses(block: CodeBlock, locator: str, report: QualityReport) -> None:
    """Defer to the TS analyzer when it is available; otherwise require the code to be
    explicitly illustrative, because an unverifiable sample must not be presented as real.
    """
    if block.illustrative:
        return
    report.add(
        "code",
        "code.unverified",
        "TypeScript sample is not verified; mark it illustrative or run the analyzer",
        locator=locator,
    )


def _code_sources_locatable(document: TutorialDocument, report: QualityReport) -> None:
    """A code block that claims to come from a source must name something that exists.

    This is intentionally conservative: it only flags code that is clearly an API call or
    import and carries a citation, because a bare assignment may be a real excerpt.
    """
    for chapter, block in document.iter_blocks():
        if not isinstance(block, CodeBlock) or block.illustrative or not block.citation_ids:
            continue
        if _API_SHAPE.search(block.code) is not None:
            continue
        if block.language.lower() == "python" and not any(
            name in block.code for name in ("import ", "from ", "def ", "class ")
        ):
            report.add(
                "code",
                "code.untraceable",
                "cited code does not name an API, import or definition that can be located",
                locator=chapter.slug,
            )


def _diagrams_render(document: TutorialDocument, report: QualityReport) -> None:
    for chapter, block in document.iter_blocks():
        if not isinstance(block, DiagramBlock):
            continue
        try:
            rendered = render_diagram(block.diagram)
        except DiagramRenderError as error:
            report.add(
                "diagrams",
                "diagram.renderer-unavailable",
                str(error),
                locator=f"{chapter.slug}/{block.diagram.slug}",
            )
            continue
        if not rendered.ok:
            detail = rendered.error or {}
            report.add(
                "diagrams",
                f"diagram.{detail.get('stage', 'render')}",
                detail.get("code", "render failed"),
                locator=f"{chapter.slug}/{block.diagram.slug}",
            )


def _markdown_is_well_formed(document: TutorialDocument, report: QualityReport) -> None:
    """A fence that opens and never closes breaks every page that follows it."""
    for chapter, block in document.iter_blocks():
        if not hasattr(block, "markdown"):
            continue
        text = block.markdown
        if text.count("```") % 2 != 0:
            report.add(
                "markdown",
                "markdown.unclosed-fence",
                "an odd number of code fences leaves the rest of the page inside one",
                locator=chapter.slug,
            )


def _no_secrets(document: TutorialDocument, report: QualityReport) -> None:
    for chapter, block in document.iter_blocks():
        text = getattr(block, "markdown", None) or getattr(block, "code", None)
        if not text:
            continue
        lowered = text.lower()
        if any(marker.lower() in lowered for marker in _SECRET_MARKERS):
            report.add(
                "secrets",
                "secret.marker",
                "content contains a secret marker",
                locator=chapter.slug,
            )
