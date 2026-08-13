"""Reducing a page to the prose a tutorial may quote.

Trafilatura does the hard part — telling an article from the navigation, header and footer
wrapped around it — and emits Markdown, which preserves the heading structure the chunker
needs to give every citation a stable anchor.

The second job is knowing when that failed. A page whose content arrives as JavaScript
leaves an almost empty document behind, and the only honest thing to report is that static
extraction was not enough. That verdict, not a guess, is what gates the browser fallback.

Everything returned here is data. Text that reads like an instruction is preserved
verbatim so it can be quoted and attributed; nothing in this module acts on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from html.parser import HTMLParser
from typing import Final

import trafilatura

from app.ingestion.web.urls import CanonicalUrl

#: Below this many characters of prose a page has not really been extracted.
MIN_SUFFICIENT_CHARACTERS: Final = 200

#: A page larger than this is not a document; refusing to parse it bounds the worker.
MAX_HTML_CHARACTERS: Final = 4 * 1024 * 1024

_HEADING_MARKER: Final = re.compile(r"^\s{0,3}#{1,6}\s+")
_FENCE: Final = re.compile(r"^\s*(```|~~~)")
_BULLET: Final = re.compile(r"^\s*([-*+]|\d+\.)\s+")
_MARKDOWN_LINK: Final = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS: Final = re.compile(r"[*_`]{1,3}")


class ExtractionOutcome(StrEnum):
    """Why the extracted text looks the way it does. Drives the browser fallback."""

    SUFFICIENT = "sufficient"
    TOO_SHORT = "too_short"
    CLIENT_RENDERED = "client_rendered"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """The normalised form of one page."""

    url: CanonicalUrl
    title: str
    markdown: str
    text: str
    outcome: ExtractionOutcome
    language: str | None = None
    published_at: date | None = None
    character_count: int = 0
    script_characters: int = 0


def extract_document(
    html: str | bytes,
    url: CanonicalUrl,
    *,
    charset: str | None = None,
    min_characters: int = MIN_SUFFICIENT_CHARACTERS,
) -> ExtractedDocument:
    """Extract the main content of ``html``, reporting how well it went."""
    source = _decode(html, charset)[:MAX_HTML_CHARACTERS]
    if not source.strip():
        return ExtractedDocument(
            url=url, title="", markdown="", text="", outcome=ExtractionOutcome.EMPTY
        )

    markdown = (
        trafilatura.extract(
            source,
            output_format="markdown",
            include_tables=True,
            include_comments=False,
            include_images=False,
            url=str(url),
        )
        or ""
    ).strip()

    statistics = _DocumentStatistics.of(source)
    metadata = _metadata(source)
    return ExtractedDocument(
        url=url,
        title=metadata.title or statistics.title,
        markdown=markdown,
        text=markdown_to_text(markdown),
        outcome=_judge(markdown, statistics, min_characters),
        language=metadata.language,
        published_at=metadata.published_at,
        character_count=len(markdown),
        script_characters=statistics.script_characters,
    )


def needs_browser_render(document: ExtractedDocument) -> bool:
    """Whether this page should be retried in the isolated browser.

    An empty body is excluded on purpose: there is no script for a browser to run, so
    rendering it would spend a container to learn the same thing again.
    """
    return document.outcome in (ExtractionOutcome.CLIENT_RENDERED, ExtractionOutcome.TOO_SHORT)


def markdown_to_text(markdown: str) -> str:
    """Project Markdown down to the plain text MySQL's FULLTEXT index should hold."""
    lines: list[str] = []
    for line in markdown.splitlines():
        if _FENCE.match(line):
            continue
        stripped = _HEADING_MARKER.sub("", line)
        stripped = _BULLET.sub("", stripped)
        stripped = _MARKDOWN_LINK.sub(r"\1", stripped)
        lines.append(_EMPHASIS.sub("", stripped).rstrip())
    return "\n".join(lines).strip()


@dataclass(frozen=True, slots=True)
class _Metadata:
    title: str = ""
    language: str | None = None
    published_at: date | None = None


def _metadata(source: str) -> _Metadata:
    try:
        document = trafilatura.extract_metadata(source)
    except Exception:  # noqa: BLE001 - metadata is a nicety; never fail a page over it
        return _Metadata()
    if document is None:
        return _Metadata()
    return _Metadata(
        title=(document.title or "").strip(),
        language=document.language or None,
        published_at=_parse_date(document.date),
    )


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _decode(html: str | bytes, charset: str | None) -> str:
    if isinstance(html, str):
        return html
    return html.decode(charset or "utf-8", errors="replace")


def _judge(
    markdown: str,
    statistics: _DocumentStatistics,
    min_characters: int,
) -> ExtractionOutcome:
    if len(markdown) >= min_characters:
        return ExtractionOutcome.SUFFICIENT
    if not markdown and not statistics.visible_characters and not statistics.script_characters:
        return ExtractionOutcome.EMPTY
    if statistics.looks_client_rendered(min_characters):
        return ExtractionOutcome.CLIENT_RENDERED
    return ExtractionOutcome.TOO_SHORT


@dataclass(frozen=True, slots=True)
class _DocumentStatistics:
    """What the raw markup itself says about why extraction came back thin."""

    title: str
    visible_characters: int
    script_characters: int

    @classmethod
    def of(cls, source: str) -> _DocumentStatistics:
        parser = _MarkupStatisticsParser()
        parser.parse(source)
        return cls(
            title=parser.title.strip(),
            visible_characters=parser.visible_characters,
            script_characters=parser.script_characters,
        )

    def looks_client_rendered(self, min_characters: int) -> bool:
        """A page whose markup is mostly script and whose body is mostly empty."""
        if self.script_characters == 0:
            return False
        return (
            self.visible_characters < min_characters
            or self.script_characters > self.visible_characters * 3
        )


class _MarkupStatisticsParser(HTMLParser):
    _IGNORED: Final = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.visible_characters = 0
        self.script_characters = 0
        self._stack: list[str] = []

    def parse(self, source: str) -> None:
        try:
            self.feed(source)
            self.close()
        except AssertionError:
            return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                continue

    def handle_data(self, data: str) -> None:
        current = self._stack[-1] if self._stack else ""
        if current == "title":
            self.title += data
        elif current in self._IGNORED:
            self.script_characters += len(data.strip())
        else:
            self.visible_characters += len(data.strip())
