"""Bounded discovery of one documentation site.

Discovery starts from the sitemap, not from links. A sitemap is the site's own inventory,
so it finds pages nothing links to and it finds them without walking the whole site; link
following only fills the gaps afterwards.

Every way out of the crawl is a bound: the scope pins scheme, host and path prefix,
robots.txt pins what the site permits, and pages, depth, bytes and wall-clock time pin the
cost. Nothing a fetched page contains can raise any of them. A link is the only thing a
page can contribute to the frontier, and it still has to clear scope, robots and the SSRF
guard before it becomes a request.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from app.clock import utcnow
from app.ingestion.web.fetcher import (
    HTML_MEDIA_TYPES,
    USER_AGENT_PRODUCT,
    FetchedResponse,
    FetchError,
    SafeFetcher,
)
from app.ingestion.web.links import extract_links, robots_meta
from app.ingestion.web.robots import RobotsPolicy, fetch_robots
from app.ingestion.web.safety import SsrfBlocked
from app.ingestion.web.sitemap import SitemapError, parse_sitemap
from app.ingestion.web.urls import CanonicalUrl, SiteScope, normalize_url

CONVENTIONAL_SITEMAP_PATH: Final = "/sitemap.xml"


class SkipReason(StrEnum):
    """Why a URL that was considered did not become a page in the corpus."""

    OUT_OF_SCOPE = "out_of_scope"
    ROBOTS = "robots_disallowed"
    NOINDEX = "page_noindex"
    DEPTH = "max_depth"
    LIMIT = "limit_reached"
    BLOCKED = "blocked_by_guard"
    FETCH_FAILED = "fetch_failed"
    MEDIA_TYPE = "unsupported_media_type"
    STATUS = "unsuccessful_status"


@dataclass(frozen=True, slots=True)
class CrawlLimits:
    """The cost ceiling of one crawl."""

    max_pages: int = 200
    max_depth: int = 3
    max_total_bytes: int = 64 * 1024 * 1024
    time_budget: float = 300.0
    #: Politeness floor. A larger ``Crawl-delay`` in robots.txt wins over this.
    request_delay: float = 0.0
    max_frontier: int = 5000
    max_sitemaps: int = 20


@dataclass(frozen=True, slots=True)
class CrawledPage:
    """One page that made it into the corpus, with its provenance."""

    url: CanonicalUrl
    requested_url: CanonicalUrl
    status_code: int
    content: bytes
    media_type: str
    charset: str | None
    fetched_at: datetime
    depth: int
    discovered_via: str
    addresses: tuple[str, ...] = ()
    redirect_chain: tuple[CanonicalUrl, ...] = ()


@dataclass(frozen=True, slots=True)
class SkippedUrl:
    """A URL that was considered and rejected, kept so a crawl can be reviewed."""

    url: CanonicalUrl
    reason: SkipReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """Everything one crawl produced, including what it refused to do."""

    seed: CanonicalUrl
    scope: SiteScope
    robots: RobotsPolicy
    started_at: datetime
    finished_at: datetime
    pages: tuple[CrawledPage, ...] = ()
    skipped: tuple[SkippedUrl, ...] = ()
    sitemap_urls: tuple[CanonicalUrl, ...] = ()
    stopped_because: str | None = None
    total_bytes: int = 0


@dataclass(slots=True)
class _Frontier:
    """The work queue, which never holds the same address twice."""

    queue: deque[tuple[CanonicalUrl, int, str]] = field(default_factory=deque)
    seen: set[CanonicalUrl] = field(default_factory=set)

    def offer(self, url: CanonicalUrl, depth: int, discovered_via: str, capacity: int) -> bool:
        if url in self.seen or len(self.seen) >= capacity:
            return False
        self.seen.add(url)
        self.queue.append((url, depth, discovered_via))
        return True

    def __bool__(self) -> bool:
        return bool(self.queue)


class SiteCrawler:
    """Walks one site inside one scope, under one set of limits."""

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        limits: CrawlLimits | None = None,
        user_agent: str = USER_AGENT_PRODUCT,
        clock: Callable[[], datetime] = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetcher = fetcher
        self._limits = limits or CrawlLimits()
        self._user_agent = user_agent
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep

    def crawl(
        self,
        seed: str | CanonicalUrl,
        *,
        include_subdomains: bool = False,
    ) -> CrawlResult:
        """Discover and fetch the site reachable from ``seed`` within its scope."""
        origin = seed if isinstance(seed, CanonicalUrl) else normalize_url(seed)
        scope = SiteScope.from_seed(origin, include_subdomains=include_subdomains)
        started_at = self._clock()
        deadline = self._monotonic() + self._limits.time_budget

        robots = fetch_robots(self._fetcher, origin, user_agent=self._user_agent)
        delay = max(robots.crawl_delay or 0.0, self._limits.request_delay)

        run = _CrawlRun(scope=scope, robots=robots, limits=self._limits)
        sitemap_urls = self._discover_sitemap_urls(origin, robots, run)
        # The seed is admitted the same way as every other candidate: a site that
        # disallows its own entry point has disallowed it for us too.
        run.consider(origin, depth=0, discovered_via="seed")
        for url in sitemap_urls:
            run.consider(url, depth=0, discovered_via="sitemap")

        first = True
        while run.frontier and run.stopped_because is None:
            if self._monotonic() > deadline:
                run.stopped_because = "time_budget"
                break
            url, depth, discovered_via = run.frontier.queue.popleft()
            if not first and delay:
                self._sleep(delay)
            first = False
            self._visit(url, depth, discovered_via, run)

        return CrawlResult(
            seed=origin,
            scope=scope,
            robots=robots,
            started_at=started_at,
            finished_at=self._clock(),
            pages=tuple(run.pages),
            skipped=tuple(run.skipped),
            sitemap_urls=tuple(sitemap_urls),
            stopped_because=run.stopped_because,
            total_bytes=run.total_bytes,
        )

    def _visit(self, url: CanonicalUrl, depth: int, discovered_via: str, run: _CrawlRun) -> None:
        response = self._safe_fetch(url, run)
        if response is None:
            return
        if not response.ok:
            run.skip(url, SkipReason.STATUS, str(response.status_code))
            return
        if response.media_type not in HTML_MEDIA_TYPES:
            run.skip(url, SkipReason.MEDIA_TYPE, response.media_type)
            return

        directives = robots_meta(response.content, user_agent=self._user_agent)
        if directives.noindex:
            run.skip(url, SkipReason.NOINDEX)
        else:
            run.record(_page_from(response, depth, discovered_via))

        if directives.nofollow or depth + 1 > self._limits.max_depth:
            return
        for link in extract_links(response.content, base=response.url):
            run.consider(link, depth=depth + 1, discovered_via="link")

    def _safe_fetch(self, url: CanonicalUrl, run: _CrawlRun) -> FetchedResponse | None:
        try:
            return self._fetcher.fetch(url)
        except SsrfBlocked as error:
            run.skip(url, SkipReason.BLOCKED, error.reason.value)
        except FetchError as error:
            run.skip(url, SkipReason.FETCH_FAILED, str(error))
        return None

    def _discover_sitemap_urls(
        self,
        origin: CanonicalUrl,
        robots: RobotsPolicy,
        run: _CrawlRun,
    ) -> tuple[CanonicalUrl, ...]:
        """Read the site's own inventory, expanding an index one level deep."""
        pending = deque(robots.sitemaps or (_conventional_sitemap(origin),))
        visited: set[CanonicalUrl] = set()
        found: dict[CanonicalUrl, None] = {}

        while pending and len(visited) < self._limits.max_sitemaps:
            location = pending.popleft()
            if location in visited or not _sitemap_in_scope(run.scope, location):
                continue
            visited.add(location)
            document = self._read_sitemap(location, run)
            if document is None:
                continue
            for child in document.sitemaps:
                pending.append(child)
            for entry in document.entries:
                found.setdefault(entry.loc, None)
        return tuple(found)

    def _read_sitemap(self, location: CanonicalUrl, run: _CrawlRun):
        response = self._safe_fetch(location, run)
        if response is None or not response.ok:
            return None
        try:
            return parse_sitemap(response.content, base=response.url)
        except SitemapError as error:
            run.skip(location, SkipReason.FETCH_FAILED, str(error))
            return None


@dataclass(slots=True)
class _CrawlRun:
    """Mutable state of one crawl, kept out of the crawler so it stays reusable."""

    scope: SiteScope
    robots: RobotsPolicy
    limits: CrawlLimits
    frontier: _Frontier = field(default_factory=_Frontier)
    pages: list[CrawledPage] = field(default_factory=list)
    skipped: list[SkippedUrl] = field(default_factory=list)
    total_bytes: int = 0
    stopped_because: str | None = None

    def consider(self, url: CanonicalUrl, *, depth: int, discovered_via: str) -> None:
        """Admit a candidate to the frontier, or record why it was refused."""
        if url in self.frontier.seen:
            return
        if not self.scope.contains(url):
            self.skip(url, SkipReason.OUT_OF_SCOPE)
            return
        if not self.robots.can_fetch(url):
            self.skip(url, SkipReason.ROBOTS)
            return
        if depth > self.limits.max_depth:
            self.skip(url, SkipReason.DEPTH, str(depth))
            return
        if not self.frontier.offer(url, depth, discovered_via, self.limits.max_frontier):
            self.skip(url, SkipReason.LIMIT, "frontier full")

    def record(self, page: CrawledPage) -> None:
        self.pages.append(page)
        self.total_bytes += len(page.content)
        if len(self.pages) >= self.limits.max_pages:
            self.stopped_because = "max_pages"
        elif self.total_bytes >= self.limits.max_total_bytes:
            self.stopped_because = "max_total_bytes"

    def skip(self, url: CanonicalUrl, reason: SkipReason, detail: str = "") -> None:
        self.skipped.append(SkippedUrl(url=url, reason=reason, detail=detail))


def _page_from(response: FetchedResponse, depth: int, discovered_via: str) -> CrawledPage:
    return CrawledPage(
        url=response.url,
        requested_url=response.requested_url,
        status_code=response.status_code,
        content=response.content,
        media_type=response.media_type,
        charset=response.charset,
        fetched_at=response.fetched_at,
        depth=depth,
        discovered_via=discovered_via,
        addresses=response.addresses,
        redirect_chain=response.redirect_chain,
    )


def _conventional_sitemap(origin: CanonicalUrl) -> CanonicalUrl:
    return CanonicalUrl(origin.scheme, origin.host, origin.port, CONVENTIONAL_SITEMAP_PATH)


def _sitemap_in_scope(scope: SiteScope, location: CanonicalUrl) -> bool:
    """A sitemap lives at the site root, which is usually outside the seed's path prefix.

    Origin still has to match — a sitemap on another host is somebody else's inventory —
    but the path prefix that bounds the crawl cannot bound where the site keeps its index.
    """
    same_host = location.host == scope.host or (
        scope.include_subdomains and location.host.endswith(f".{scope.host}")
    )
    return location.scheme == scope.scheme and location.port == scope.port and same_host
