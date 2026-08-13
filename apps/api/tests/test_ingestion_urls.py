"""URL canonicalisation, the first gate every fetched address passes through."""

from __future__ import annotations

import pytest

from app.ingestion.web.urls import (
    CanonicalUrl,
    SiteScope,
    UnsafeUrl,
    UnsupportedScheme,
    normalize_url,
)


def test_normalisation_lowercases_the_scheme_and_host_and_drops_the_fragment() -> None:
    url = normalize_url("HTTPS://Docs.Example.COM/Guide#installation")

    assert str(url) == "https://docs.example.com/Guide"
    assert url.scheme == "https"
    assert url.host == "docs.example.com"


def test_the_default_port_is_removed_and_an_explicit_one_is_kept() -> None:
    assert str(normalize_url("https://docs.example.test:443/a")) == "https://docs.example.test/a"
    assert str(normalize_url("http://docs.example.test:80/a")) == "http://docs.example.test/a"
    assert str(normalize_url("http://docs.example.test:8443/a")) == "http://docs.example.test:8443/a"


def test_dot_segments_are_resolved_and_an_empty_path_becomes_a_slash() -> None:
    assert str(normalize_url("https://docs.example.test/a/./b/../c")) == (
        "https://docs.example.test/a/c"
    )
    assert str(normalize_url("https://docs.example.test")) == "https://docs.example.test/"


def test_a_relative_reference_resolves_against_its_base() -> None:
    resolved = normalize_url("../sibling", base="https://docs.example.test/a/b/c")

    assert str(resolved) == "https://docs.example.test/a/sibling"


def test_tracking_parameters_are_dropped_and_the_rest_are_ordered() -> None:
    url = normalize_url("https://docs.example.test/g?b=2&utm_source=news&a=1&gclid=xyz")

    assert str(url) == "https://docs.example.test/g?a=1&b=2"


def test_a_query_that_is_entirely_tracking_leaves_no_question_mark() -> None:
    assert str(normalize_url("https://docs.example.test/g?utm_medium=email")) == (
        "https://docs.example.test/g"
    )


def test_percent_encoding_is_normalised_without_decoding_reserved_characters() -> None:
    """``%2F`` must survive: decoding it would silently change which path is requested."""
    url = normalize_url("https://docs.example.test/%7euser/a%2fb")

    assert str(url) == "https://docs.example.test/~user/a%2Fb"


def test_an_internationalised_host_is_encoded_as_punycode() -> None:
    url = normalize_url("https://Bücher.example.test/über")

    assert url.host == "xn--bcher-kva.example.test"
    assert str(url).endswith("/%C3%BCber")


def test_a_trailing_dot_on_the_host_is_removed() -> None:
    """``example.test.`` and ``example.test`` are the same host; only one may be crawled."""
    assert normalize_url("https://docs.example.test./a").host == "docs.example.test"


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://files.example.test/pub",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "gopher://example.test:70/",
    ],
)
def test_every_non_http_scheme_is_refused(raw: str) -> None:
    with pytest.raises(UnsupportedScheme):
        normalize_url(raw)


def test_credentials_in_the_authority_are_refused() -> None:
    """``https://docs.example.test@169.254.169.254/`` reads as a trusted host to a human."""
    with pytest.raises(UnsafeUrl):
        normalize_url("https://docs.example.test@169.254.169.254/latest/meta-data/")


@pytest.mark.parametrize(
    "raw",
    [
        "https://docs.example.test/a\r\nX-Injected: 1",
        "https://docs.example.test/a\tb",
        "https://docs.example.test/\x00",
        "https://docs.example.test/\x7f",
    ],
)
def test_control_characters_are_refused_rather_than_silently_stripped(raw: str) -> None:
    with pytest.raises(UnsafeUrl):
        normalize_url(raw)


def test_an_over_long_url_is_refused() -> None:
    with pytest.raises(UnsafeUrl):
        normalize_url("https://docs.example.test/" + "a" * 4096)


def test_an_ipv6_literal_keeps_its_brackets_in_the_serialised_form() -> None:
    url = normalize_url("https://[2606:2800:220:1:248:1893:25C8:1946]/a")

    assert url.host == "2606:2800:220:1:248:1893:25c8:1946"
    assert str(url) == "https://[2606:2800:220:1:248:1893:25c8:1946]/a"
    assert url.is_ip_literal


def test_the_host_header_carries_a_non_default_port_but_not_a_default_one() -> None:
    assert normalize_url("https://docs.example.test/a").host_header == "docs.example.test"
    assert normalize_url("http://docs.example.test:8080/a").host_header == (
        "docs.example.test:8080"
    )


def test_canonical_urls_that_normalise_alike_compare_equal() -> None:
    assert normalize_url("https://Docs.Example.test:443/a/../b?x=1#f") == normalize_url(
        "https://docs.example.test/b?x=1"
    )


class TestSiteScope:
    def test_by_default_only_the_seed_host_itself_is_in_scope(self) -> None:
        scope = SiteScope.from_seed(normalize_url("https://docs.example.test/guide/"))

        assert scope.contains(normalize_url("https://docs.example.test/guide/install"))
        assert not scope.contains(normalize_url("https://api.docs.example.test/guide/x"))
        assert not scope.contains(normalize_url("https://evil.test/guide/x"))

    def test_subdomains_are_included_only_when_asked_for(self) -> None:
        scope = SiteScope.from_seed(
            normalize_url("https://example.test/"), include_subdomains=True
        )

        assert scope.contains(normalize_url("https://docs.example.test/a"))
        assert scope.contains(normalize_url("https://example.test/a"))

    def test_a_host_that_merely_ends_with_the_seed_host_is_out_of_scope(self) -> None:
        """``notexample.test`` must not match ``example.test`` as a plain suffix."""
        scope = SiteScope.from_seed(
            normalize_url("https://example.test/"), include_subdomains=True
        )

        assert not scope.contains(normalize_url("https://notexample.test/a"))

    def test_the_seed_path_prefix_bounds_the_crawl(self) -> None:
        scope = SiteScope.from_seed(normalize_url("https://example.test/docs/"))

        assert scope.contains(normalize_url("https://example.test/docs/install"))
        assert not scope.contains(normalize_url("https://example.test/blog/post"))

    def test_a_scheme_downgrade_is_out_of_scope(self) -> None:
        scope = SiteScope.from_seed(normalize_url("https://example.test/"))

        assert not scope.contains(normalize_url("http://example.test/a"))


def test_canonical_url_is_hashable_so_the_frontier_can_deduplicate_on_it() -> None:
    seen: set[CanonicalUrl] = {normalize_url("https://example.test/a")}
    seen.add(normalize_url("https://example.test/a?utm_source=x"))

    assert len(seen) == 1
