"""Reading a page for the two things a crawler is allowed to learn from it.

Only ``<a href>`` extends the frontier. A URL that merely appears in prose does not,
because page text is untrusted input and must never be able to widen the crawl — which is
exactly what an instruction embedded in a page would try to do.

Parsing uses the standard library's tolerant HTML parser rather than an XML one: real
pages are not well-formed, and a strict parser would drop whole sites over a stray tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

from app.ingestion.web.urls import CanonicalUrl, UnsafeUrl, normalize_url

#: A page offering more links than this is a site map or an attack, not a document.
MAX_LINKS_PER_PAGE: Final = 500


@dataclass(frozen=True, slots=True)
class RobotsDirectives:
    """What a page's ``robots`` meta tag asks of us."""

    noindex: bool = False
    nofollow: bool = False


def extract_links(
    html: str | bytes,
    *,
    base: CanonicalUrl,
    max_links: int = MAX_LINKS_PER_PAGE,
) -> tuple[CanonicalUrl, ...]:
    """Return the distinct canonical addresses this page links to, in document order."""
    parser = _LinkParser(base)
    parser.feed_html(html)
    return tuple(parser.links)[:max_links]


def robots_meta(html: str | bytes, *, user_agent: str = "robots") -> RobotsDirectives:
    """Read the page-level crawl directives, for this crawler or for every crawler."""
    parser = _MetaParser({"robots", user_agent.lower()})
    parser.feed_html(html)
    return RobotsDirectives(noindex=parser.noindex, nofollow=parser.nofollow)


class _TolerantParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

    def feed_html(self, html: str | bytes) -> None:
        text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
        try:
            self.feed(text)
            self.close()
        except AssertionError:
            # ``HTMLParser`` asserts on a handful of malformed constructs. A broken page is
            # a page with fewer links, not a failed crawl.
            return


class _LinkParser(_TolerantParser):
    def __init__(self, base: CanonicalUrl) -> None:
        super().__init__()
        self._base: CanonicalUrl | str = base
        self.links: dict[CanonicalUrl, None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag == "base" and attributes.get("href"):
            self._set_base(attributes["href"])
            return
        if tag != "a":
            return
        if "nofollow" in attributes.get("rel", "").lower().split():
            return
        self._add(attributes.get("href", ""))

    def _set_base(self, href: str) -> None:
        try:
            self._base = normalize_url(href, base=str(self._base))
        except UnsafeUrl:
            return

    def _add(self, href: str) -> None:
        candidate = href.strip()
        if not candidate or candidate.startswith("#"):
            return
        try:
            self.links.setdefault(normalize_url(candidate, base=str(self._base)), None)
        except UnsafeUrl:
            return


class _MetaParser(_TolerantParser):
    def __init__(self, names: set[str]) -> None:
        super().__init__()
        self._names = names
        self.noindex = False
        self.nofollow = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if attributes.get("name", "").lower() not in self._names:
            return
        directives = {token.strip().lower() for token in attributes.get("content", "").split(",")}
        self.noindex = self.noindex or "noindex" in directives or "none" in directives
        self.nofollow = self.nofollow or "nofollow" in directives or "none" in directives
