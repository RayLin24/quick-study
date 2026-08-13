"""One spelling per address.

Two jobs that look like one. Canonicalisation gives the crawler a key it can deduplicate
and cite on, so ``https://Docs.Example.test:443/a/../b#top`` and
``https://docs.example.test/b`` are recognised as the same page. Refusing malformed input
keeps the shapes that only exist to fool a reader — embedded credentials, control
characters, non-HTTP schemes — from ever reaching the network layer.

Canonicalisation is *not* an access decision. Whether an address may be fetched is decided
in :mod:`app.ingestion.web.safety` against the addresses it actually resolves to.
"""

from __future__ import annotations

import ipaddress
import re
import string
from dataclasses import dataclass
from typing import Final, Self
from urllib.parse import SplitResult, quote, unquote, urljoin, urlsplit

#: Long enough for any real documentation URL, short enough to bound every downstream
#: buffer, index and log line.
MAX_URL_LENGTH: Final = 2048

MAX_HOST_LENGTH: Final = 253
MAX_LABEL_LENGTH: Final = 63

DEFAULT_PORTS: Final[dict[str, int]] = {"http": 80, "https": 443}

#: Analytics parameters carry no content, so keeping them would split one page into many.
TRACKING_PARAMETERS: Final[frozenset[str]] = frozenset(
    {
        "dclid",
        "fbclid",
        "gbraid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "mkt_tok",
        "spm",
        "wbraid",
        "yclid",
        "_hsenc",
        "_hsmi",
        "_ga",
        "_gl",
    }
)
TRACKING_PARAMETER_PREFIXES: Final[tuple[str, ...]] = ("utm_",)

_CONTROL_CHARACTERS: Final = re.compile(r"[\x00-\x1f\x7f]")
_ASCII_LABEL: Final = re.compile(r"\A[a-z0-9_-]+\Z")
_HEX_DIGITS: Final = frozenset(string.hexdigits)
_UNRESERVED: Final = frozenset(string.ascii_letters + string.digits + "-._~")

#: Characters that stay literal in a path. Everything else is percent-encoded, so a
#: normalised path can be pasted into a request line without further escaping.
_PATH_SAFE: Final = "/:@!$&'()*+,;=-._~"
_QUERY_SAFE: Final = "/:@!$'()*+,;=-._~"


class UnsafeUrl(ValueError):
    """Raised for input that will not be turned into a request under any policy."""


class UnsupportedScheme(UnsafeUrl):
    """Raised for anything that is not ``http`` or ``https``."""


@dataclass(frozen=True, slots=True)
class CanonicalUrl:
    """An absolute HTTP(S) address in exactly one normalised form.

    Instances are hashable and compare by value, which is what lets the crawl frontier,
    the duplicate index and citations agree on page identity without extra bookkeeping.
    """

    scheme: str
    host: str
    port: int
    path: str
    query: str = ""

    def __str__(self) -> str:
        suffix = f"?{self.query}" if self.query else ""
        return f"{self.scheme}://{self.host_header}{self.path}{suffix}"

    @property
    def is_ip_literal(self) -> bool:
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            return False
        return True

    @property
    def is_default_port(self) -> bool:
        return self.port == DEFAULT_PORTS[self.scheme]

    @property
    def bracketed_host(self) -> str:
        """The host as it appears inside a URL: IPv6 literals need brackets."""
        return f"[{self.host}]" if ":" in self.host else self.host

    @property
    def host_header(self) -> str:
        """What belongs in the ``Host`` header, which omits a default port."""
        if self.is_default_port:
            return self.bracketed_host
        return f"{self.bracketed_host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host_header}"

    def with_host(self, host: str) -> CanonicalUrl:
        """Return the same request aimed at ``host``.

        Used to connect to an address the SSRF guard has already validated while the
        original name is still carried in the ``Host`` header and the TLS handshake.
        """
        return CanonicalUrl(self.scheme, host, self.port, self.path, self.query)


def normalize_url(raw: str, *, base: str | CanonicalUrl | None = None) -> CanonicalUrl:
    """Return the canonical form of ``raw``, refusing anything unfit to be requested.

    ``base`` resolves a relative reference, which is how links harvested from a page are
    turned into absolute addresses.
    """
    if not isinstance(raw, str):
        raise UnsafeUrl("a URL must be a string")
    candidate = raw.strip()
    if not candidate:
        raise UnsafeUrl("empty URL")
    _reject_control_characters(candidate)
    if base is not None:
        base_text = str(base)
        _reject_control_characters(base_text)
        candidate = urljoin(base_text, candidate)
    if len(candidate) > MAX_URL_LENGTH:
        raise UnsafeUrl(f"URL longer than {MAX_URL_LENGTH} characters")

    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise UnsupportedScheme(f"{scheme or '(relative)'!r} is not an http(s) scheme")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrl("credentials in the authority are not accepted")
    if parts.hostname is None:
        raise UnsafeUrl("URL has no host")

    host = _normalise_host(parts.hostname)
    port = _normalise_port(parts, scheme)
    path = _remove_dot_segments(_normalise_percent_encoding(parts.path, _PATH_SAFE))
    query = _normalise_query(parts.query)
    return CanonicalUrl(scheme=scheme, host=host, port=port, path=path, query=query)


@dataclass(frozen=True, slots=True)
class SiteScope:
    """The boundary a crawl may not cross.

    Deliberately narrow by default: one scheme, one host, one path prefix. Widening to
    subdomains is an explicit decision because "same site" is not a property a crawler can
    infer safely without a public suffix list.
    """

    scheme: str
    host: str
    port: int
    path_prefix: str
    include_subdomains: bool = False

    @classmethod
    def from_seed(
        cls,
        seed: CanonicalUrl,
        *,
        include_subdomains: bool = False,
        path_prefix: str | None = None,
    ) -> Self:
        prefix = path_prefix if path_prefix is not None else _directory_of(seed.path)
        return cls(
            scheme=seed.scheme,
            host=seed.host,
            port=seed.port,
            path_prefix=prefix,
            include_subdomains=include_subdomains,
        )

    def contains(self, url: CanonicalUrl) -> bool:
        return (
            url.scheme == self.scheme
            and url.port == self.port
            and self._host_matches(url.host)
            and url.path.startswith(self.path_prefix)
        )

    def _host_matches(self, host: str) -> bool:
        if host == self.host:
            return True
        return self.include_subdomains and host.endswith(f".{self.host}")


def _directory_of(path: str) -> str:
    """Return the deepest directory of ``path``, which bounds a crawl seeded at a page."""
    return path if path.endswith("/") else path[: path.rfind("/") + 1] or "/"


def _reject_control_characters(value: str) -> None:
    """Refuse rather than strip.

    ``urlsplit`` silently removes tabs and newlines, so a URL carrying a smuggled header
    would normalise to something harmless-looking while telling us nothing about the
    input that produced it.
    """
    if _CONTROL_CHARACTERS.search(value):
        raise UnsafeUrl("control characters are not allowed in a URL")


def _normalise_host(raw: str) -> str:
    host = raw.strip().lower().rstrip(".")
    if not host:
        raise UnsafeUrl("URL has an empty host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if len(host) > MAX_HOST_LENGTH:
        raise UnsafeUrl(f"host longer than {MAX_HOST_LENGTH} characters")
    return ".".join(_encode_label(label) for label in host.split("."))


def _encode_label(label: str) -> str:
    if not label:
        raise UnsafeUrl("host contains an empty label")
    if not label.isascii():
        try:
            label = label.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise UnsafeUrl(f"host label {label!r} is not encodable as IDNA") from error
    if len(label) > MAX_LABEL_LENGTH:
        raise UnsafeUrl(f"host label longer than {MAX_LABEL_LENGTH} characters")
    if not _ASCII_LABEL.match(label):
        raise UnsafeUrl(f"host label {label!r} contains characters that are not allowed")
    return label


def _normalise_port(parts: SplitResult, scheme: str) -> int:
    try:
        port = parts.port
    except ValueError as error:
        raise UnsafeUrl("URL has an unparsable port") from error
    if port is None:
        return DEFAULT_PORTS[scheme]
    if not 1 <= port <= 65535:
        raise UnsafeUrl(f"port {port} is out of range")
    return port


def _normalise_percent_encoding(value: str, safe: str) -> str:
    """Decode what is safe to decode, uppercase the rest, encode what was left literal.

    Only unreserved characters are decoded. Decoding a reserved one would change which
    resource is addressed: ``/a%2Fb`` is a single path segment, ``/a/b`` is two.
    """
    output: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        character = value[index]
        if character == "%" and index + 2 < length + 1:
            triplet = value[index + 1 : index + 3]
            if len(triplet) == 2 and set(triplet) <= _HEX_DIGITS:
                decoded = chr(int(triplet, 16))
                output.append(decoded if decoded in _UNRESERVED else f"%{triplet.upper()}")
                index += 3
                continue
        output.append(quote(character, safe=safe))
        index += 1
    return "".join(output)


def _remove_dot_segments(path: str) -> str:
    """Apply RFC 3986 dot-segment removal so one resource has one path."""
    trailing_slash = path.endswith("/")
    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    resolved = "/" + "/".join(segments)
    if trailing_slash and not resolved.endswith("/"):
        resolved += "/"
    return resolved


def _normalise_query(query: str) -> str:
    """Drop analytics parameters and order the rest so equivalent queries look equal."""
    if not query:
        return ""
    retained: list[tuple[str, str, str]] = []
    for segment in query.split("&"):
        if not segment:
            continue
        key, separator, value = segment.partition("=")
        if _is_tracking_parameter(unquote(key).lower()):
            continue
        retained.append(
            (
                _normalise_percent_encoding(key, _QUERY_SAFE),
                separator,
                _normalise_percent_encoding(value, _QUERY_SAFE),
            )
        )
    retained.sort()
    return "&".join(f"{key}{separator}{value}" for key, separator, value in retained)


def _is_tracking_parameter(key: str) -> bool:
    return key in TRACKING_PARAMETERS or key.startswith(TRACKING_PARAMETER_PREFIXES)
