"""The HTTP client: every hop re-checked, every budget bounded.

Automatic redirect following is the single most common way an SSRF check is defeated, so
the tests here care as much about *what was never contacted* as about what came back.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator

import pytest
from ingestion_support import StubSite, mapping_resolver, public_resolver

from app.ingestion.web.fetcher import (
    CompressionBombDetected,
    FetchLimits,
    FetchTimeout,
    ResponseTooLarge,
    SafeFetcher,
    TooManyRedirects,
    UnsupportedMediaType,
)
from app.ingestion.web.safety import AddressGuard, BlockReason, SsrfBlocked
from app.ingestion.web.urls import normalize_url

PAGE = "<html><head><title>Install</title></head><body><p>Install it.</p></body></html>"


@pytest.fixture
def site() -> StubSite:
    return StubSite()


def fetcher_for(
    site: StubSite,
    *hosts: str,
    limits: FetchLimits | None = None,
    resolver=None,
) -> Iterator[SafeFetcher]:
    guard = AddressGuard(resolver=resolver or public_resolver(*hosts))
    return SafeFetcher(
        guard=guard, transport=site.transport, limits=limits or FetchLimits()
    )


def test_a_page_comes_back_with_the_metadata_a_snapshot_needs(site: StubSite) -> None:
    site.add("https://docs.example.test/install", PAGE)

    with fetcher_for(site, "docs.example.test") as fetcher:
        response = fetcher.fetch("https://docs.example.test/install")

    assert response.status_code == 200
    assert response.content == PAGE.encode()
    assert response.media_type == "text/html"
    assert response.charset == "utf-8"
    assert str(response.url) == "https://docs.example.test/install"
    assert response.fetched_at.tzinfo is not None
    assert response.addresses == ("93.184.216.34",)


def test_the_request_is_sent_to_the_validated_address_while_the_name_stays_in_the_header(
    site: StubSite,
) -> None:
    """Check and connect must refer to the same machine, or the check proves nothing."""
    site.add("https://docs.example.test/install", PAGE)

    with fetcher_for(site, "docs.example.test") as fetcher:
        fetcher.fetch("https://docs.example.test/install")

    assert site.addresses_contacted() == {"93.184.216.34"}
    assert site.hosts_contacted() == ["docs.example.test"]


def test_a_name_that_answers_publicly_then_privately_is_never_dialled_privately(
    site: StubSite,
) -> None:
    """DNS rebinding: the address cleared by the guard is the address connected to.

    A second resolution between the check and the connection is exactly the window this
    closes, so the private answer must not appear among the contacted addresses.
    """
    site.add("https://rebind.example.test/page", PAGE)
    answers = iter([("93.184.216.34",), ("127.0.0.1",), ("127.0.0.1",)])

    def rebinding(host: str, port: int) -> tuple[str, ...]:
        return next(answers)

    with fetcher_for(site, resolver=rebinding) as fetcher:
        fetcher.fetch("https://rebind.example.test/page")

        assert site.addresses_contacted() == {"93.184.216.34"}
        with pytest.raises(SsrfBlocked) as caught:
            fetcher.fetch("https://rebind.example.test/page")

    assert caught.value.reason is BlockReason.LOOPBACK
    assert "127.0.0.1" not in site.addresses_contacted()


def test_a_redirect_is_followed_only_after_the_new_target_clears_the_guard(
    site: StubSite,
) -> None:
    site.add_redirect("https://docs.example.test/old", "https://docs.example.test/new")
    site.add("https://docs.example.test/new", PAGE)

    with fetcher_for(site, "docs.example.test") as fetcher:
        response = fetcher.fetch("https://docs.example.test/old")

    assert str(response.url) == "https://docs.example.test/new"
    assert str(response.requested_url) == "https://docs.example.test/old"
    assert [str(hop) for hop in response.redirect_chain] == ["https://docs.example.test/old"]


def test_a_redirect_to_the_cloud_metadata_service_is_refused_without_being_contacted(
    site: StubSite,
) -> None:
    site.add_redirect(
        "https://docs.example.test/old", "http://169.254.169.254/latest/meta-data/iam/"
    )

    with fetcher_for(site, "docs.example.test") as fetcher, pytest.raises(SsrfBlocked) as caught:
        fetcher.fetch("https://docs.example.test/old")

    assert caught.value.reason is BlockReason.CLOUD_METADATA
    assert site.addresses_contacted() == {"93.184.216.34"}
    assert "169.254.169.254" not in site.addresses_contacted()


def test_a_redirect_to_a_name_that_resolves_internally_is_refused(site: StubSite) -> None:
    """The second hop gets a full resolution and a full verdict, not a cached one."""
    site.add_redirect("https://docs.example.test/old", "https://intranet.example.test/secrets")
    resolver = mapping_resolver(
        {"docs.example.test": ("93.184.216.34",), "intranet.example.test": ("10.0.0.7",)}
    )

    with fetcher_for(site, resolver=resolver) as fetcher, pytest.raises(SsrfBlocked) as caught:
        fetcher.fetch("https://docs.example.test/old")

    assert caught.value.reason is BlockReason.PRIVATE
    assert site.hosts_contacted() == ["docs.example.test"]


def test_a_redirect_to_a_non_http_scheme_is_refused(site: StubSite) -> None:
    site.add_redirect("https://docs.example.test/old", "file:///etc/passwd")

    with fetcher_for(site, "docs.example.test") as fetcher, pytest.raises(SsrfBlocked) as caught:
        fetcher.fetch("https://docs.example.test/old")

    assert caught.value.reason is BlockReason.SCHEME


def test_a_relative_location_header_resolves_against_the_url_that_produced_it(
    site: StubSite,
) -> None:
    site.add_redirect("https://docs.example.test/a/b/old", "../new")
    site.add("https://docs.example.test/a/new", PAGE)

    with fetcher_for(site, "docs.example.test") as fetcher:
        response = fetcher.fetch("https://docs.example.test/a/b/old")

    assert str(response.url) == "https://docs.example.test/a/new"


def test_a_redirect_loop_stops_at_the_configured_hop_count(site: StubSite) -> None:
    site.add_redirect("https://docs.example.test/a", "https://docs.example.test/b")
    site.add_redirect("https://docs.example.test/b", "https://docs.example.test/a")

    with (
        fetcher_for(site, "docs.example.test", limits=FetchLimits(max_redirects=3)) as fetcher,
        pytest.raises(TooManyRedirects),
    ):
        fetcher.fetch("https://docs.example.test/a")

    assert len(site.requests) == 4


def test_the_underlying_client_never_follows_redirects_by_itself(site: StubSite) -> None:
    """Defence in depth: the manual loop is only safe if httpx is not racing it."""
    with fetcher_for(site, "docs.example.test") as fetcher:
        assert fetcher.client.follow_redirects is False


def test_a_response_larger_than_the_budget_is_refused(site: StubSite) -> None:
    site.add("https://docs.example.test/big", b"x" * 4096)

    with (
        fetcher_for(
            site, "docs.example.test", limits=FetchLimits(max_response_bytes=1024)
        ) as fetcher,
        pytest.raises(ResponseTooLarge),
    ):
        fetcher.fetch("https://docs.example.test/big")


def test_an_oversized_content_length_is_refused_before_the_body_is_read(
    site: StubSite,
) -> None:
    site.add(
        "https://docs.example.test/big",
        b"x" * 32,
        headers={"content-length": "99999999"},
    )

    with (
        fetcher_for(
            site, "docs.example.test", limits=FetchLimits(max_response_bytes=1024)
        ) as fetcher,
        pytest.raises(ResponseTooLarge) as caught,
    ):
        fetcher.fetch("https://docs.example.test/big")

    assert "content-length" in str(caught.value).lower()


def test_a_payload_that_expands_far_beyond_what_arrived_is_refused(site: StubSite) -> None:
    """A decompression bomb is bounded by the ratio, not only by the byte ceiling."""
    site.add_gzip("https://docs.example.test/bomb", b"\0" * (4 * 1024 * 1024))

    with (
        fetcher_for(
            site,
            "docs.example.test",
            limits=FetchLimits(
                max_response_bytes=8 * 1024 * 1024,
                max_compression_ratio=50.0,
                compression_ratio_floor_bytes=16 * 1024,
            ),
        ) as fetcher,
        pytest.raises(CompressionBombDetected),
    ):
        fetcher.fetch("https://docs.example.test/bomb")


def test_ordinary_compressed_html_is_not_mistaken_for_a_bomb(site: StubSite) -> None:
    site.add_gzip("https://docs.example.test/page", (PAGE * 200).encode())

    with fetcher_for(site, "docs.example.test") as fetcher:
        response = fetcher.fetch("https://docs.example.test/page")

    assert response.content == (PAGE * 200).encode()


def test_the_gzip_body_really_is_compressed_in_the_stub() -> None:
    """Guards the bomb test itself: a stub that forgot to compress would prove nothing."""
    assert len(gzip.compress(b"\0" * 65536)) < 1024


def test_a_media_type_outside_the_accepted_set_is_refused(site: StubSite) -> None:
    site.add(
        "https://docs.example.test/binary",
        b"\x7fELF",
        content_type="application/x-executable",
    )

    with (
        fetcher_for(site, "docs.example.test") as fetcher,
        pytest.raises(UnsupportedMediaType),
    ):
        fetcher.fetch("https://docs.example.test/binary", accept_media_types=("text/html",))


def test_a_non_success_status_is_returned_rather_than_raised(site: StubSite) -> None:
    """robots.txt handling depends on telling 404 apart from 503."""
    site.add("https://docs.example.test/gone", b"", status_code=404, content_type="text/plain")

    with fetcher_for(site, "docs.example.test") as fetcher:
        response = fetcher.fetch("https://docs.example.test/gone")

    assert response.status_code == 404
    assert response.ok is False


def test_the_crawler_identifies_itself(site: StubSite) -> None:
    site.add("https://docs.example.test/a", PAGE)

    with fetcher_for(site, "docs.example.test") as fetcher:
        fetcher.fetch("https://docs.example.test/a")

    assert "QuickStudyBot" in site.requests[0].headers["user-agent"]


def test_the_wall_clock_budget_covers_the_whole_redirect_chain(site: StubSite) -> None:
    site.add_redirect("https://docs.example.test/a", "https://docs.example.test/b")
    site.add("https://docs.example.test/b", PAGE)
    ticks = iter([0.0, 0.0, 100.0, 200.0])

    fetcher = SafeFetcher(
        guard=AddressGuard(resolver=public_resolver("docs.example.test")),
        transport=site.transport,
        limits=FetchLimits(total_timeout=10.0),
        monotonic=lambda: next(ticks),
    )
    with fetcher, pytest.raises(FetchTimeout):
        fetcher.fetch("https://docs.example.test/a")


def test_the_fetcher_refuses_a_url_it_cannot_canonicalise(site: StubSite) -> None:
    with fetcher_for(site, "docs.example.test") as fetcher, pytest.raises(SsrfBlocked):
        fetcher.fetch("ftp://docs.example.test/a")


def test_a_canonical_url_may_be_passed_straight_through(site: StubSite) -> None:
    site.add("https://docs.example.test/a", PAGE)

    with fetcher_for(site, "docs.example.test") as fetcher:
        response = fetcher.fetch(normalize_url("https://docs.example.test/a"))

    assert response.ok
