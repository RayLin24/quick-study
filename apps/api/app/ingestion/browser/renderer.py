"""Driving the browser, with every request it makes routed back through the guard.

A rendered page is a program. Left alone it will fetch analytics, fonts, third-party
bundles and anything else it likes, and each of those is an outbound request this system
would be making on a stranger's behalf. So the router below is installed for ``**/*``:
the page may talk to the site being rendered and nothing else, and even that has to clear
the SSRF guard again, because a subresource host resolves independently of the page's.

Playwright is imported lazily. The renderer is a fallback, and a deployment that never
needs it should not fail to start for want of a browser.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Protocol

from app.clock import utcnow
from app.ingestion.web.fetcher import DEFAULT_USER_AGENT
from app.ingestion.web.safety import AddressGuard, SsrfBlocked
from app.ingestion.web.urls import CanonicalUrl, SiteScope, UnsafeUrl, normalize_url

ROUTE_PATTERN: Final = "**/*"

#: Chromium flags that only tighten the sandbox. ``--no-sandbox`` is deliberately absent.
LAUNCH_ARGUMENTS: Final[tuple[str, ...]] = (
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
)


class BrowserUnavailable(RuntimeError):
    """Raised when no browser is installed. Not a page failure; an environment failure."""


class RenderFailed(RuntimeError):
    """Raised when the page could not be rendered within its budget."""


class RouteVerdict(StrEnum):
    """Why one request a rendered page made was allowed or refused."""

    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    BLOCKED = "blocked_by_guard"
    BAD_SCHEME = "scheme_not_allowed"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    allowed: bool
    reason: RouteVerdict
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """One page to render, with the budget it may spend doing it."""

    target: CanonicalUrl
    timeout_seconds: float = 20.0
    wait_until: str = "networkidle"


@dataclass(frozen=True, slots=True)
class RenderResult:
    """The markup a browser produced, and what it was stopped from fetching."""

    url: CanonicalUrl
    html: str
    status_code: int
    rendered_at: datetime
    blocked_requests: tuple[str, ...] = field(default=())


class BrowserRenderer(Protocol):
    """The seam the crawler depends on, so a renderer can be swapped or stubbed."""

    def render(self, request: RenderRequest) -> RenderResult: ...


def route_decision(url: str, *, scope: SiteScope, guard: AddressGuard) -> RouteDecision:
    """Decide one request a rendered page wants to make.

    Scope is checked before DNS so an out-of-scope host is refused without a lookup, which
    also means a page cannot use subresource loads to probe what resolves.
    """
    try:
        canonical = normalize_url(url)
    except UnsafeUrl as error:
        return RouteDecision(False, RouteVerdict.BAD_SCHEME, str(error))
    if not scope.contains(canonical):
        return RouteDecision(False, RouteVerdict.OUT_OF_SCOPE, canonical.host)
    try:
        guard.check_url(canonical)
    except SsrfBlocked as error:
        return RouteDecision(False, RouteVerdict.BLOCKED, error.reason.value)
    return RouteDecision(True, RouteVerdict.IN_SCOPE)


class PlaywrightRenderer:
    """Renders one page per call in a fresh, disposable browser context."""

    def __init__(
        self,
        *,
        guard: AddressGuard,
        scope: SiteScope,
        playwright_factory: Callable[[], Any] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._guard = guard
        self._scope = scope
        self._factory = playwright_factory or _default_playwright_factory
        self._user_agent = user_agent
        self._clock = clock

    def render(self, request: RenderRequest) -> RenderResult:
        """Render ``request.target``, refusing before startup if it may not be fetched."""
        target = self._guard.check_url(request.target)
        blocked: list[str] = []
        try:
            driver = self._factory()
        except ImportError as error:
            raise BrowserUnavailable(f"playwright is not installed: {error}") from error

        with driver as playwright:
            browser = playwright.chromium.launch(headless=True, args=list(LAUNCH_ARGUMENTS))
            try:
                return self._render_in(browser, request, target.url, blocked)
            finally:
                browser.close()

    def _render_in(
        self,
        browser: Any,
        request: RenderRequest,
        url: CanonicalUrl,
        blocked: list[str],
    ) -> RenderResult:
        context = browser.new_context(
            user_agent=self._user_agent,
            java_script_enabled=True,
            service_workers="block",
            accept_downloads=False,
            ignore_https_errors=False,
            bypass_csp=False,
            locale="en-US",
        )
        try:
            page = context.new_page()
            page.route(ROUTE_PATTERN, self._router(blocked))
            try:
                response = page.goto(
                    str(url),
                    wait_until=request.wait_until,
                    timeout=int(request.timeout_seconds * 1000),
                )
            except Exception as error:  # noqa: BLE001 - every driver failure is one outcome
                raise RenderFailed(f"{url}: {error}") from error
            return RenderResult(
                url=url,
                html=page.content(),
                status_code=getattr(response, "status", 0) or 0,
                rendered_at=self._clock(),
                blocked_requests=tuple(blocked),
            )
        finally:
            context.close()

    def _router(self, blocked: list[str]) -> Callable[[Any], None]:
        """Return the handler Playwright calls for every request the page attempts."""

        def handle(route: Any, *_: Any) -> None:
            url = str(getattr(route.request, "url", ""))
            decision = route_decision(url, scope=self._scope, guard=self._guard)
            if decision.allowed:
                route.continue_()
                return
            blocked.append(url)
            route.abort()

        return handle


def _default_playwright_factory() -> Any:
    from playwright.sync_api import sync_playwright

    return sync_playwright()
