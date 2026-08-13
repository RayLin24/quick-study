"""robots.txt is the site's own statement of scope, and it is binding here."""

from __future__ import annotations

import pytest
from ingestion_support import StubSite, public_resolver

from app.ingestion.web.fetcher import USER_AGENT_PRODUCT, FetchLimits, SafeFetcher
from app.ingestion.web.robots import (
    MAX_ROBOTS_BYTES,
    RobotsPolicy,
    fetch_robots,
    parse_robots,
    robots_url_for,
)
from app.ingestion.web.safety import AddressGuard
from app.ingestion.web.urls import normalize_url


def policy(text: str, *, user_agent: str = USER_AGENT_PRODUCT) -> RobotsPolicy:
    return parse_robots(text, user_agent=user_agent)


def test_a_disallowed_prefix_covers_the_directory_below_it() -> None:
    rules = policy("User-agent: *\nDisallow: /private")

    assert rules.can_fetch("https://docs.example.test/public/a")
    assert not rules.can_fetch("https://docs.example.test/private")
    assert not rules.can_fetch("https://docs.example.test/private/keys")


def test_an_empty_disallow_permits_everything() -> None:
    assert policy("User-agent: *\nDisallow:").can_fetch("https://docs.example.test/anything")


def test_a_bare_slash_disallows_the_whole_site() -> None:
    rules = policy("User-agent: *\nDisallow: /")

    assert not rules.can_fetch("https://docs.example.test/")
    assert not rules.can_fetch("https://docs.example.test/docs/install")


def test_a_missing_robots_file_permits_everything() -> None:
    assert RobotsPolicy.allow_all().can_fetch("https://docs.example.test/anything")


def test_the_longest_matching_rule_wins_regardless_of_the_order_it_appears_in() -> None:
    rules = policy("User-agent: *\nDisallow: /docs\nAllow: /docs/public")

    assert not rules.can_fetch("https://docs.example.test/docs/internal")
    assert rules.can_fetch("https://docs.example.test/docs/public/guide")


def test_allow_wins_when_two_rules_match_with_equal_specificity() -> None:
    rules = policy("User-agent: *\nDisallow: /a\nAllow: /a")

    assert rules.can_fetch("https://docs.example.test/a")


def test_a_wildcard_matches_any_run_of_characters() -> None:
    rules = policy("User-agent: *\nDisallow: /*/draft")

    assert not rules.can_fetch("https://docs.example.test/guides/draft")
    assert not rules.can_fetch("https://docs.example.test/a/b/draft")
    assert rules.can_fetch("https://docs.example.test/guides/final")


def test_a_dollar_sign_anchors_the_end_of_the_path() -> None:
    rules = policy("User-agent: *\nDisallow: /*.pdf$")

    assert not rules.can_fetch("https://docs.example.test/manual.pdf")
    assert rules.can_fetch("https://docs.example.test/manual.pdf.html")


def test_the_query_string_takes_part_in_matching() -> None:
    rules = policy("User-agent: *\nDisallow: /search?")

    assert not rules.can_fetch("https://docs.example.test/search?q=secret")
    assert rules.can_fetch("https://docs.example.test/searching")


def test_the_group_naming_this_crawler_is_preferred_over_the_catch_all() -> None:
    rules = policy(
        "User-agent: *\nDisallow: /\n\nUser-agent: QuickStudyBot\nDisallow: /admin\n"
    )

    assert rules.can_fetch("https://docs.example.test/docs/install")
    assert not rules.can_fetch("https://docs.example.test/admin")


def test_user_agent_matching_ignores_case_and_the_version_suffix() -> None:
    rules = policy("User-agent: quickstudybot\nDisallow: /admin", user_agent="QuickStudyBot/0.1")

    assert not rules.can_fetch("https://docs.example.test/admin")


def test_consecutive_user_agent_lines_share_one_set_of_rules() -> None:
    rules = policy("User-agent: SomeoneElse\nUser-agent: QuickStudyBot\nDisallow: /shared")

    assert not rules.can_fetch("https://docs.example.test/shared")


def test_a_group_for_another_crawler_does_not_apply_to_us() -> None:
    rules = policy("User-agent: OtherBot\nDisallow: /\n\nUser-agent: *\nDisallow: /admin")

    assert rules.can_fetch("https://docs.example.test/docs")
    assert not rules.can_fetch("https://docs.example.test/admin")


def test_directives_comments_and_stray_whitespace_are_tolerated() -> None:
    rules = policy(
        "# a comment\n  USER-AGENT :  *  \n"
        "DISALLOW: /private   # trailing comment\n"
        "nonsense line without a colon\n"
        "Unknown-Directive: value\n"
    )

    assert not rules.can_fetch("https://docs.example.test/private")
    assert rules.can_fetch("https://docs.example.test/public")


def test_sitemaps_are_collected_from_anywhere_in_the_file() -> None:
    rules = policy(
        "Sitemap: https://docs.example.test/sitemap.xml\n"
        "User-agent: *\nDisallow: /private\n"
        "Sitemap: https://docs.example.test/sitemap-news.xml\n"
    )

    assert [str(entry) for entry in rules.sitemaps] == [
        "https://docs.example.test/sitemap.xml",
        "https://docs.example.test/sitemap-news.xml",
    ]


def test_an_unusable_sitemap_line_is_skipped_rather_than_failing_the_file() -> None:
    rules = policy("Sitemap: javascript:alert(1)\nSitemap: https://docs.example.test/s.xml\n")

    assert [str(entry) for entry in rules.sitemaps] == ["https://docs.example.test/s.xml"]


def test_a_crawl_delay_is_reported_so_the_crawler_can_honour_it() -> None:
    assert policy("User-agent: *\nCrawl-delay: 2.5\nDisallow:").crawl_delay == 2.5
    assert policy("User-agent: *\nDisallow:").crawl_delay is None
    assert policy("User-agent: *\nCrawl-delay: soon\nDisallow:").crawl_delay is None


def test_percent_encoded_paths_are_compared_in_one_encoding() -> None:
    rules = policy("User-agent: *\nDisallow: /caf%C3%A9")

    assert not rules.can_fetch("https://docs.example.test/caf%C3%A9/menu")


def test_rules_before_any_user_agent_line_are_ignored() -> None:
    """A stray rule with no group cannot silently apply to every crawler."""
    rules = policy("Disallow: /\nUser-agent: *\nDisallow: /private")

    assert rules.can_fetch("https://docs.example.test/docs")


class TestRobotsUrl:
    def test_it_is_the_origin_plus_robots_txt(self) -> None:
        source = normalize_url("https://docs.example.test/deep/page?x=1")

        assert str(robots_url_for(source)) == "https://docs.example.test/robots.txt"

    def test_a_non_default_port_is_part_of_the_origin(self) -> None:
        source = normalize_url("http://docs.example.test:8080/a")

        assert str(robots_url_for(source)) == "http://docs.example.test:8080/robots.txt"


class TestFetchRobots:
    @pytest.fixture
    def site(self) -> StubSite:
        return StubSite()

    def fetcher(self, site: StubSite) -> SafeFetcher:
        return SafeFetcher(
            guard=AddressGuard(resolver=public_resolver("docs.example.test")),
            transport=site.transport,
            limits=FetchLimits(),
        )

    def test_a_served_file_is_parsed(self, site: StubSite) -> None:
        site.add(
            "https://docs.example.test/robots.txt",
            "User-agent: *\nDisallow: /private\n",
            content_type="text/plain",
        )

        with self.fetcher(site) as fetcher:
            rules = fetch_robots(fetcher, normalize_url("https://docs.example.test/guide"))

        assert not rules.can_fetch("https://docs.example.test/private")

    def test_a_site_without_robots_txt_may_be_crawled(self, site: StubSite) -> None:
        with self.fetcher(site) as fetcher:
            rules = fetch_robots(fetcher, normalize_url("https://docs.example.test/guide"))

        assert rules.can_fetch("https://docs.example.test/anything")

    def test_a_server_error_stops_the_crawl_instead_of_guessing(self, site: StubSite) -> None:
        """RFC 9309: unreachable is not the same as absent, and must be read as a refusal."""
        site.add(
            "https://docs.example.test/robots.txt",
            b"",
            status_code=503,
            content_type="text/plain",
        )

        with self.fetcher(site) as fetcher:
            rules = fetch_robots(fetcher, normalize_url("https://docs.example.test/guide"))

        assert not rules.can_fetch("https://docs.example.test/anything")

    def test_a_transport_failure_also_stops_the_crawl(self, site: StubSite) -> None:
        resolver = public_resolver("other.example.test")

        fetcher = SafeFetcher(guard=AddressGuard(resolver=resolver), transport=site.transport)
        with fetcher:
            rules = fetch_robots(fetcher, normalize_url("https://docs.example.test/guide"))

        assert not rules.can_fetch("https://docs.example.test/anything")

    def test_an_oversized_robots_file_is_truncated_at_the_documented_limit(
        self, site: StubSite
    ) -> None:
        padding = "# " + "p" * 200 + "\n"
        body = padding * (MAX_ROBOTS_BYTES // len(padding) + 10) + "User-agent: *\nDisallow: /\n"
        site.add("https://docs.example.test/robots.txt", body, content_type="text/plain")

        with self.fetcher(site) as fetcher:
            rules = fetch_robots(fetcher, normalize_url("https://docs.example.test/guide"))

        assert rules.can_fetch("https://docs.example.test/anything")
