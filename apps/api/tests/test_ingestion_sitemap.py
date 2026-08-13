"""Sitemaps are the cheapest and most complete way to learn what a documentation site has.

They are also attacker-controlled XML, so the parser has to be as suspicious as the fetcher.
"""

from __future__ import annotations

import gzip

import pytest

from app.ingestion.web.sitemap import (
    MAX_SITEMAP_ENTRIES,
    SitemapError,
    parse_sitemap,
)
from app.ingestion.web.urls import normalize_url

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://docs.example.test/install</loc>
    <lastmod>2026-03-04T10:00:00+00:00</lastmod>
    <priority>0.8</priority>
  </url>
  <url><loc>https://docs.example.test/configure</loc></url>
</urlset>
"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.example.test/sitemap-guides.xml</loc></sitemap>
  <sitemap><loc>https://docs.example.test/sitemap-api.xml</loc></sitemap>
</sitemapindex>
"""


def test_a_urlset_yields_its_locations_with_the_metadata_that_was_present() -> None:
    sitemap = parse_sitemap(URLSET)

    assert not sitemap.is_index
    assert [str(entry.loc) for entry in sitemap.entries] == [
        "https://docs.example.test/install",
        "https://docs.example.test/configure",
    ]
    first = sitemap.entries[0]
    assert first.lastmod is not None
    assert first.lastmod.year == 2026
    assert first.priority == 0.8
    assert sitemap.entries[1].lastmod is None


def test_a_sitemap_index_yields_child_sitemaps_and_no_pages() -> None:
    sitemap = parse_sitemap(INDEX)

    assert sitemap.is_index
    assert sitemap.entries == ()
    assert [str(child) for child in sitemap.sitemaps] == [
        "https://docs.example.test/sitemap-guides.xml",
        "https://docs.example.test/sitemap-api.xml",
    ]


def test_a_gzipped_sitemap_is_decompressed() -> None:
    sitemap = parse_sitemap(gzip.compress(URLSET.encode()))

    assert len(sitemap.entries) == 2


def test_a_sitemap_without_the_conventional_namespace_still_parses() -> None:
    sitemap = parse_sitemap(
        "<urlset><url><loc>https://docs.example.test/a</loc></url></urlset>"
    )

    assert [str(entry.loc) for entry in sitemap.entries] == ["https://docs.example.test/a"]


def test_a_relative_location_resolves_against_the_sitemap_url() -> None:
    sitemap = parse_sitemap(
        "<urlset><url><loc>/guide/install</loc></url></urlset>",
        base=normalize_url("https://docs.example.test/sitemaps/pages.xml"),
    )

    assert [str(entry.loc) for entry in sitemap.entries] == [
        "https://docs.example.test/guide/install"
    ]


def test_a_location_that_cannot_be_canonicalised_is_skipped_not_fatal() -> None:
    sitemap = parse_sitemap(
        "<urlset>"
        "<url><loc>javascript:alert(1)</loc></url>"
        "<url><loc>https://docs.example.test/ok</loc></url>"
        "<url><loc></loc></url>"
        "</urlset>"
    )

    assert [str(entry.loc) for entry in sitemap.entries] == ["https://docs.example.test/ok"]


def test_an_unparsable_lastmod_leaves_the_entry_usable() -> None:
    sitemap = parse_sitemap(
        "<urlset><url><loc>https://docs.example.test/a</loc>"
        "<lastmod>whenever</lastmod></url></urlset>"
    )

    assert sitemap.entries[0].lastmod is None


def test_a_date_only_lastmod_is_understood() -> None:
    sitemap = parse_sitemap(
        "<urlset><url><loc>https://docs.example.test/a</loc>"
        "<lastmod>2026-03-04</lastmod></url></urlset>"
    )

    assert sitemap.entries[0].lastmod is not None
    assert sitemap.entries[0].lastmod.tzinfo is not None


def test_a_document_type_declaration_is_refused_outright() -> None:
    """Entity declarations are the whole attack surface, so no document may carry one."""
    hostile = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<urlset><url><loc>&xxe;</loc></url></urlset>"
    )

    with pytest.raises(SitemapError):
        parse_sitemap(hostile)


def test_a_billion_laughs_expansion_never_starts() -> None:
    hostile = (
        "<!DOCTYPE lolz ["
        '<!ENTITY lol "lol">'
        "<!ENTITY lol2 '&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;'>"
        "]>"
        "<urlset><url><loc>&lol2;</loc></url></urlset>"
    )

    with pytest.raises(SitemapError):
        parse_sitemap(hostile)


def test_content_that_is_not_xml_is_refused() -> None:
    with pytest.raises(SitemapError):
        parse_sitemap("<html><body>not a sitemap</body></html>")


def test_malformed_xml_is_refused() -> None:
    with pytest.raises(SitemapError):
        parse_sitemap("<urlset><url><loc>https://a.test/</loc>")


def test_the_number_of_entries_is_capped() -> None:
    body = "".join(
        f"<url><loc>https://docs.example.test/p{index}</loc></url>" for index in range(120)
    )
    sitemap = parse_sitemap(f"<urlset>{body}</urlset>", max_entries=50)

    assert len(sitemap.entries) == 50
    assert sitemap.truncated


def test_the_default_entry_cap_is_the_documented_one() -> None:
    assert MAX_SITEMAP_ENTRIES == 50_000


def test_a_sitemap_larger_than_the_byte_budget_is_refused() -> None:
    with pytest.raises(SitemapError):
        parse_sitemap(b"<urlset>" + b"<!-- padding -->" * 100, max_bytes=256)


def test_duplicate_locations_are_reported_once() -> None:
    sitemap = parse_sitemap(
        "<urlset>"
        "<url><loc>https://docs.example.test/a</loc></url>"
        "<url><loc>https://docs.example.test/a?utm_source=x</loc></url>"
        "</urlset>"
    )

    assert len(sitemap.entries) == 1
