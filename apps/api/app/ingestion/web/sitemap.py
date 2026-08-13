"""Sitemap parsing, treated as parsing of hostile XML.

A sitemap is the cheapest complete inventory a documentation site can give us, which is
why discovery starts here rather than with link following. It is also a file an attacker
controls, so this module refuses any document carrying a ``DOCTYPE``. That single rule
removes external entity expansion and entity-expansion denial of service together, and it
costs nothing: no real sitemap declares a doctype.

Individual bad entries are skipped rather than failing the file. One unusable ``<loc>``
should not cost a site its entire inventory.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from xml.etree import ElementTree

from app.ingestion.web.urls import CanonicalUrl, UnsafeUrl, normalize_url

#: The per-file ceiling in the sitemaps.org protocol.
MAX_SITEMAP_ENTRIES: Final = 50_000

#: The protocol's uncompressed size ceiling.
MAX_SITEMAP_BYTES: Final = 50 * 1024 * 1024

_GZIP_MAGIC: Final = b"\x1f\x8b"
_DOCTYPE: Final = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_ROOT_URLSET: Final = "urlset"
_ROOT_INDEX: Final = "sitemapindex"


class SitemapError(ValueError):
    """Raised when a document is not a sitemap this system will read."""


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """One page a site says it has."""

    loc: CanonicalUrl
    lastmod: datetime | None = None
    priority: float | None = None


@dataclass(frozen=True, slots=True)
class Sitemap:
    """A parsed sitemap: either pages or a list of further sitemaps, never both."""

    entries: tuple[SitemapEntry, ...] = ()
    sitemaps: tuple[CanonicalUrl, ...] = ()
    truncated: bool = False

    @property
    def is_index(self) -> bool:
        return bool(self.sitemaps) and not self.entries


def parse_sitemap(
    data: bytes | str,
    *,
    base: CanonicalUrl | None = None,
    max_entries: int = MAX_SITEMAP_ENTRIES,
    max_bytes: int = MAX_SITEMAP_BYTES,
) -> Sitemap:
    """Parse a sitemap or sitemap index, refusing anything that is not one."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    payload = _decompress(payload, max_bytes)
    if len(payload) > max_bytes:
        raise SitemapError(f"sitemap larger than {max_bytes} bytes")
    if _DOCTYPE.search(payload):
        raise SitemapError("sitemaps with a document type or entity declaration are refused")

    try:
        # DOCTYPE and ENTITY are refused above, so no external entities or entity
        # expansion can reach this parse; stdlib ElementTree is safe for the grammar
        # that remains.
        root = ElementTree.fromstring(payload)  # noqa: S314
    except ElementTree.ParseError as error:
        raise SitemapError(f"not well-formed XML: {error}") from error

    tag = _local_name(root.tag)
    if tag == _ROOT_INDEX:
        children, truncated = _collect_locations(root, "sitemap", base, max_entries)
        return Sitemap(sitemaps=tuple(children), truncated=truncated)
    if tag == _ROOT_URLSET:
        return _parse_urlset(root, base, max_entries)
    raise SitemapError(f"unexpected root element {tag!r}")


def _parse_urlset(
    root: ElementTree.Element,
    base: CanonicalUrl | None,
    max_entries: int,
) -> Sitemap:
    entries: list[SitemapEntry] = []
    seen: set[CanonicalUrl] = set()
    truncated = False
    for element in root:
        if _local_name(element.tag) != "url":
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
        location = _location_of(element, base)
        if location is None or location in seen:
            continue
        seen.add(location)
        entries.append(
            SitemapEntry(
                loc=location,
                lastmod=_parse_lastmod(_child_text(element, "lastmod")),
                priority=_parse_priority(_child_text(element, "priority")),
            )
        )
    return Sitemap(entries=tuple(entries), truncated=truncated)


def _collect_locations(
    root: ElementTree.Element,
    child_tag: str,
    base: CanonicalUrl | None,
    max_entries: int,
) -> tuple[list[CanonicalUrl], bool]:
    found: list[CanonicalUrl] = []
    seen: set[CanonicalUrl] = set()
    for element in root:
        if _local_name(element.tag) != child_tag:
            continue
        if len(found) >= max_entries:
            return found, True
        location = _location_of(element, base)
        if location is None or location in seen:
            continue
        seen.add(location)
        found.append(location)
    return found, False


def _decompress(payload: bytes, max_bytes: int) -> bytes:
    """Sitemaps are commonly served gzipped; the expansion stays inside the byte budget."""
    if not payload.startswith(_GZIP_MAGIC):
        return payload
    try:
        with gzip.GzipFile(fileobj=_BytesReader(payload)) as handle:
            expanded = handle.read(max_bytes + 1)
    except (OSError, EOFError) as error:
        raise SitemapError(f"unreadable gzip sitemap: {error}") from error
    if len(expanded) > max_bytes:
        raise SitemapError(f"sitemap expands beyond {max_bytes} bytes")
    return expanded


def _local_name(tag: str) -> str:
    """Strip the XML namespace. Sitemaps in the wild use several, or none at all."""
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ElementTree.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _location_of(element: ElementTree.Element, base: CanonicalUrl | None) -> CanonicalUrl | None:
    raw = _child_text(element, "loc")
    if not raw:
        return None
    try:
        return normalize_url(raw, base=base)
    except UnsafeUrl:
        return None


def _parse_lastmod(raw: str) -> datetime | None:
    """Accept the W3C datetime profile the protocol specifies, and a bare date."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), datetime.min.time())
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_priority(raw: str) -> float | None:
    try:
        priority = float(raw)
    except ValueError:
        return None
    return priority if 0.0 <= priority <= 1.0 else None


class _BytesReader:
    """A minimal read-only file object over bytes, so gzip can stream out of memory."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._offset = offset
        elif whence == 1:
            self._offset += offset
        else:
            self._offset = len(self._payload) + offset
        return self._offset

    def tell(self) -> int:
        return self._offset

    def seekable(self) -> bool:
        return True
