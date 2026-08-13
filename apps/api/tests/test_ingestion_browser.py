"""The browser fallback: last resort, tightest box.

Running a real browser over untrusted pages is the largest attack surface in this system,
so it is entered only when static extraction demonstrably failed, and everything it can
reach is enumerated rather than assumed. The container spec and the request router are
both pure values, which is what makes those guarantees testable without a browser present.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ingestion_support import mapping_resolver

from app.ingestion.browser.policy import RenderBudget, should_render
from app.ingestion.browser.renderer import (
    BrowserUnavailable,
    PlaywrightRenderer,
    RenderFailed,
    RenderRequest,
    RouteVerdict,
    route_decision,
)
from app.ingestion.browser.sandbox import SandboxSpec, SandboxSpecError
from app.ingestion.web.extract import ExtractedDocument, ExtractionOutcome
from app.ingestion.web.safety import AddressGuard, SsrfBlocked
from app.ingestion.web.urls import SiteScope, normalize_url

TARGET = normalize_url("https://docs.example.test/app")
SCOPE = SiteScope.from_seed(normalize_url("https://docs.example.test/"))
RESOLVER = mapping_resolver(
    {
        "docs.example.test": ("93.184.216.34",),
        "cdn.example.test": ("93.184.216.40",),
        "intranet.example.test": ("10.0.0.7",),
    }
)


def extraction(outcome: ExtractionOutcome) -> ExtractedDocument:
    return ExtractedDocument(
        url=TARGET, title="", markdown="", text="", outcome=outcome
    )


class TestRenderPolicy:
    @pytest.mark.parametrize(
        "outcome", [ExtractionOutcome.CLIENT_RENDERED, ExtractionOutcome.TOO_SHORT]
    )
    def test_a_page_static_extraction_could_not_read_is_worth_rendering(
        self, outcome: ExtractionOutcome
    ) -> None:
        assert should_render(extraction(outcome), RenderBudget())

    @pytest.mark.parametrize(
        "outcome", [ExtractionOutcome.SUFFICIENT, ExtractionOutcome.EMPTY]
    )
    def test_a_page_that_read_fine_or_had_no_body_is_not_rendered(
        self, outcome: ExtractionOutcome
    ) -> None:
        assert not should_render(extraction(outcome), RenderBudget())

    def test_the_number_of_renders_in_one_crawl_is_capped(self) -> None:
        budget = RenderBudget(max_renders=2)

        assert should_render(extraction(ExtractionOutcome.CLIENT_RENDERED), budget)
        budget.spend()
        budget.spend()

        assert not should_render(extraction(ExtractionOutcome.CLIENT_RENDERED), budget)
        assert budget.exhausted


class TestSandboxSpec:
    def spec(self, **overrides) -> SandboxSpec:
        defaults = {
            "image": "quick-study-renderer:local",
            "egress_network": "quick-study-egress",
            "target": TARGET,
            "addresses": ("93.184.216.34",),
        }
        return SandboxSpec(**{**defaults, **overrides})

    def test_the_filesystem_is_read_only_with_only_a_scratch_tmpfs(self) -> None:
        command = self.spec().docker_command()

        assert "--read-only" in command
        assert any(argument.startswith("--tmpfs=/tmp") for argument in command)

    def test_no_host_path_is_ever_mounted(self) -> None:
        command = self.spec().docker_command()

        assert not any(
            argument.startswith(("-v", "--volume", "--mount")) for argument in command
        )

    def test_the_container_drops_every_capability_and_cannot_regain_privilege(self) -> None:
        command = self.spec().docker_command()

        assert "--cap-drop=ALL" in command
        assert "--security-opt=no-new-privileges" in command
        assert "--user=65534:65534" in command

    def test_egress_goes_through_the_dedicated_network_and_never_the_host(self) -> None:
        command = self.spec().docker_command()

        assert "--network=quick-study-egress" in command
        assert "--network=host" not in command
        assert "--network=bridge" not in command

    def test_the_validated_address_is_pinned_so_the_container_cannot_re_resolve(self) -> None:
        command = self.spec().docker_command()

        assert "--add-host=docs.example.test:93.184.216.34" in command

    def test_resource_limits_bound_a_hostile_page(self) -> None:
        command = self.spec().docker_command()

        assert "--memory=512m" in command
        assert "--cpus=1.0" in command
        assert "--pids-limit=256" in command
        assert "--rm" in command
        assert "--init" in command

    def test_the_container_carries_no_application_environment(self) -> None:
        """The renderer needs no secret, so it is given none and inherits none."""
        command = self.spec().docker_command()
        passed = [argument for argument in command if argument.startswith(("-e", "--env"))]

        assert passed == ["--env=RENDER_TIMEOUT_MS=20000"]

    def test_the_target_is_passed_as_an_argument_not_a_shell_string(self) -> None:
        command = self.spec().docker_command()

        assert command[0] == "docker"
        assert command[-1] == str(TARGET)
        assert not any(argument in ("sh", "-c", "bash") for argument in command)

    def test_a_target_with_no_cleared_address_cannot_be_rendered(self) -> None:
        with pytest.raises(SandboxSpecError):
            self.spec(addresses=()).docker_command()

    def test_the_egress_network_must_be_named(self) -> None:
        with pytest.raises(SandboxSpecError):
            self.spec(egress_network="").docker_command()

    def test_the_host_network_is_refused_outright(self) -> None:
        with pytest.raises(SandboxSpecError):
            self.spec(egress_network="host").docker_command()


class TestRouteDecision:
    def guard(self) -> AddressGuard:
        return AddressGuard(resolver=RESOLVER)

    def test_a_request_to_the_page_being_rendered_is_allowed(self) -> None:
        verdict = route_decision(
            "https://docs.example.test/static/app.js", scope=SCOPE, guard=self.guard()
        )

        assert verdict.allowed
        assert verdict.reason == RouteVerdict.IN_SCOPE

    def test_a_subresource_on_another_public_host_is_blocked(self) -> None:
        """A rendered page may not become a request generator for the whole internet."""
        verdict = route_decision(
            "https://cdn.example.test/lib.js", scope=SCOPE, guard=self.guard()
        )

        assert not verdict.allowed
        assert verdict.reason == RouteVerdict.OUT_OF_SCOPE

    def test_an_in_scope_host_that_now_resolves_internally_is_blocked(self) -> None:
        """Scope is not a substitute for the guard: the same name can change address."""
        rebound = AddressGuard(resolver=mapping_resolver({"docs.example.test": ("10.0.0.7",)}))

        verdict = route_decision(
            "https://docs.example.test/static/app.js", scope=SCOPE, guard=rebound
        )

        assert not verdict.allowed
        assert verdict.reason == RouteVerdict.BLOCKED

    def test_an_out_of_scope_host_is_refused_without_being_resolved(self) -> None:
        """Subresource loads must not become a way to probe what a name resolves to."""
        looked_up: list[str] = []

        def recording(host: str, port: int) -> tuple[str, ...]:
            looked_up.append(host)
            return ("93.184.216.40",)

        verdict = route_decision(
            "https://intranet.example.test/keys",
            scope=SCOPE,
            guard=AddressGuard(resolver=recording),
        )

        assert not verdict.allowed
        assert verdict.reason == RouteVerdict.OUT_OF_SCOPE
        assert looked_up == []

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "data:text/html,x", "chrome://settings"]
    )
    def test_a_non_http_request_is_blocked(self, url: str) -> None:
        verdict = route_decision(url, scope=SCOPE, guard=self.guard())

        assert not verdict.allowed
        assert verdict.reason == RouteVerdict.BAD_SCHEME


class FakeResponse:
    def __init__(self, status: int = 200, url: str = str(TARGET)) -> None:
        self.status = status
        self.url = url


class FakePage:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser
        self.routes: list[tuple[str, object]] = []
        self.closed = False

    def route(self, pattern: str, handler: object) -> None:
        self.routes.append((pattern, handler))

    def goto(self, url: str, **kwargs: object) -> FakeResponse:
        self._browser.navigations.append((url, kwargs))
        if self._browser.navigation_error:
            raise self._browser.navigation_error
        return FakeResponse(url=url)

    def content(self) -> str:
        return self._browser.html

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, browser: FakeBrowser, options: dict[str, object]) -> None:
        self._browser = browser
        self.options = options
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage(self._browser)
        self._browser.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, html: str = "<html><body>rendered</body></html>") -> None:
        self.html = html
        self.launch_options: dict[str, object] = {}
        self.contexts: list[FakeContext] = []
        self.pages: list[FakePage] = []
        self.navigations: list[tuple[str, dict[str, object]]] = []
        self.navigation_error: Exception | None = None
        self.closed = False

    def launch(self, **options: object) -> FakeBrowser:
        self.launch_options = options
        return self

    def new_context(self, **options: object) -> FakeContext:
        context = FakeContext(self, options)
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed = True

    @property
    def chromium(self) -> FakeBrowser:
        return self

    def __enter__(self) -> FakeBrowser:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class TestPlaywrightRenderer:
    def renderer(self, browser: FakeBrowser) -> PlaywrightRenderer:
        return PlaywrightRenderer(
            guard=AddressGuard(resolver=RESOLVER),
            scope=SCOPE,
            playwright_factory=lambda: browser,
            clock=lambda: datetime(2026, 3, 4, 10, 30, tzinfo=UTC),
        )

    def test_the_rendered_markup_comes_back_with_its_address(self) -> None:
        browser = FakeBrowser()

        result = self.renderer(browser).render(RenderRequest(target=TARGET))

        assert result.html == "<html><body>rendered</body></html>"
        assert result.url == TARGET
        assert result.rendered_at.tzinfo is not None

    def test_the_browser_context_is_created_with_the_hardening_options(self) -> None:
        browser = FakeBrowser()

        self.renderer(browser).render(RenderRequest(target=TARGET))

        options = browser.contexts[0].options
        assert options["service_workers"] == "block"
        assert options["accept_downloads"] is False
        assert options["ignore_https_errors"] is False
        assert options["bypass_csp"] is False
        assert "QuickStudyBot" in str(options["user_agent"])

    def test_the_browser_is_launched_headless_without_a_sandbox_escape_hatch(self) -> None:
        browser = FakeBrowser()

        self.renderer(browser).render(RenderRequest(target=TARGET))

        assert browser.launch_options["headless"] is True
        assert "--no-sandbox" not in browser.launch_options.get("args", [])

    def test_every_request_the_page_makes_is_routed_through_the_guard(self) -> None:
        browser = FakeBrowser()

        self.renderer(browser).render(RenderRequest(target=TARGET))

        assert browser.pages[0].routes
        assert browser.pages[0].routes[0][0] == "**/*"

    def test_the_target_must_clear_the_guard_before_a_browser_is_started(self) -> None:
        browser = FakeBrowser()
        renderer = PlaywrightRenderer(
            guard=AddressGuard(resolver=RESOLVER),
            scope=SCOPE,
            playwright_factory=lambda: browser,
        )

        with pytest.raises(SsrfBlocked):
            renderer.render(
                RenderRequest(target=normalize_url("https://intranet.example.test/x"))
            )

        assert browser.contexts == []

    def test_a_navigation_failure_is_reported_and_the_browser_is_closed(self) -> None:
        browser = FakeBrowser()
        browser.navigation_error = RuntimeError("Timeout 20000ms exceeded")

        with pytest.raises(RenderFailed):
            self.renderer(browser).render(RenderRequest(target=TARGET))

        assert browser.contexts[0].closed
        assert browser.closed

    def test_the_navigation_carries_a_bounded_timeout(self) -> None:
        browser = FakeBrowser()

        self.renderer(browser).render(RenderRequest(target=TARGET, timeout_seconds=7.5))

        _, options = browser.navigations[0]
        assert options["timeout"] == 7500

    def test_a_missing_playwright_install_is_reported_as_such(self) -> None:
        def unavailable():
            raise ImportError("No module named 'playwright'")

        renderer = PlaywrightRenderer(
            guard=AddressGuard(resolver=RESOLVER), scope=SCOPE, playwright_factory=unavailable
        )

        with pytest.raises(BrowserUnavailable):
            renderer.render(RenderRequest(target=TARGET))
