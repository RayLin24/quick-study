"""The only way this system makes an outbound web request.

Two properties matter more than anything else here. The HTTP client never follows a
redirect on its own, because a followed redirect is a request that was never checked; the
loop below re-runs the full SSRF verdict for every single hop. And the connection is made
to the address the guard cleared, with the site name carried in the ``Host`` header and the
TLS handshake, so a name cannot answer publicly for the check and privately for the dial.

Everything else is a budget: bytes, expansion ratio, hops and wall-clock time are all
bounded, so a hostile or merely broken site cannot consume a worker indefinitely.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Final, Self

import httpx

from app.clock import utcnow
from app.ingestion.web.safety import AddressGuard, BlockReason, ResolvedTarget, SsrfBlocked
from app.ingestion.web.urls import CanonicalUrl, UnsafeUrl, UnsupportedScheme, normalize_url

#: The product token robots.txt groups are matched against. Keep the two in step.
USER_AGENT_PRODUCT: Final = "QuickStudyBot"
DEFAULT_USER_AGENT: Final = (
    f"{USER_AGENT_PRODUCT}/0.1 (+https://example.invalid/quick-study; respects robots.txt)"
)

REDIRECT_STATUS_CODES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})

HTML_MEDIA_TYPES: Final[tuple[str, ...]] = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
)


class FetchError(Exception):
    """Base class for a request that could not be completed under policy."""


class ResponseTooLarge(FetchError):
    """Raised when a response exceeds the byte budget, declared or actual."""


class CompressionBombDetected(FetchError):
    """Raised when a body expands far beyond the bytes that arrived on the wire."""


class TooManyRedirects(FetchError):
    """Raised when a redirect chain outlives its hop budget."""


class FetchTimeout(FetchError):
    """Raised when the wall-clock budget for a whole fetch is spent."""


class UnsupportedMediaType(FetchError):
    """Raised when a response is not something this ingestion path can read."""


class TransportFailure(FetchError):
    """Raised when the connection itself failed."""


@dataclass(frozen=True, slots=True)
class FetchLimits:
    """Every bound a single fetch is subject to.

    ``max_compression_ratio`` only applies once ``compression_ratio_floor_bytes`` has been
    decoded: small responses routinely compress spectacularly and are harmless, so judging
    them on ratio alone produces nothing but false positives.
    """

    max_response_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 5
    max_compression_ratio: float = 100.0
    compression_ratio_floor_bytes: int = 1024 * 1024
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    write_timeout: float = 5.0
    pool_timeout: float = 5.0
    total_timeout: float = 45.0

    def as_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    """One completed response, with everything a snapshot and a citation need."""

    url: CanonicalUrl
    requested_url: CanonicalUrl
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    media_type: str
    charset: str | None
    fetched_at: datetime
    addresses: tuple[str, ...]
    redirect_chain: tuple[CanonicalUrl, ...] = ()

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        """Decode the body, never failing on a mislabelled or lying charset."""
        return self.content.decode(self.charset or "utf-8", errors="replace")


class SafeFetcher:
    """A guarded HTTP client. One instance owns one connection pool."""

    def __init__(
        self,
        *,
        guard: AddressGuard | None = None,
        transport: httpx.BaseTransport | None = None,
        limits: FetchLimits | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        clock: Callable[[], datetime] = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._guard = guard or AddressGuard()
        self._limits = limits or FetchLimits()
        self._clock = clock
        self._monotonic = monotonic
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=self._limits.as_httpx_timeout(),
            headers={
                "user-agent": user_agent,
                "accept-language": "en;q=0.9, *;q=0.5",
            },
        )

    @property
    def client(self) -> httpx.Client:
        return self._client

    @property
    def guard(self) -> AddressGuard:
        return self._guard

    @property
    def limits(self) -> FetchLimits:
        return self._limits

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch(
        self,
        url: str | CanonicalUrl,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        accept_media_types: Sequence[str] | None = None,
    ) -> FetchedResponse:
        """Fetch ``url``, re-checking the destination at every redirect."""
        requested = _canonicalise(url)
        deadline = self._monotonic() + self._limits.total_timeout
        current = requested
        chain: list[CanonicalUrl] = []

        for _ in range(self._limits.max_redirects + 1):
            if self._monotonic() > deadline:
                raise FetchTimeout(f"budget of {self._limits.total_timeout}s spent on {requested}")
            target = self._guard.check_url(current)
            response = self._exchange(target, method, headers, accept_media_types)
            location = _redirect_location(response)
            if location is None:
                return self._materialise(
                    response, requested, current, target, tuple(chain), accept_media_types
                )
            response.close()
            chain.append(current)
            current = _canonicalise(location, base=current)

        raise TooManyRedirects(
            f"more than {self._limits.max_redirects} redirects starting at {requested}"
        )

    def _exchange(
        self,
        target: ResolvedTarget,
        method: str,
        headers: Mapping[str, str] | None,
        accept_media_types: Sequence[str] | None,
    ) -> httpx.Response:
        request_headers: dict[str, str] = {"host": target.url.host_header}
        if accept_media_types:
            request_headers["accept"] = ", ".join(accept_media_types)
        request_headers.update({key.lower(): value for key, value in (headers or {}).items()})

        extensions = {"sni_hostname": target.url.host} if target.url.scheme == "https" else {}
        request = self._client.build_request(
            method,
            str(target.connect_url),
            headers=request_headers,
            extensions=extensions,
        )
        try:
            return self._client.send(request, stream=True)
        except httpx.TimeoutException as error:
            raise FetchTimeout(f"{target.url}: {error}") from error
        except httpx.HTTPError as error:
            raise TransportFailure(f"{target.url}: {error}") from error

    def _materialise(
        self,
        response: httpx.Response,
        requested: CanonicalUrl,
        final: CanonicalUrl,
        target: ResolvedTarget,
        chain: tuple[CanonicalUrl, ...],
        accept_media_types: Sequence[str] | None,
    ) -> FetchedResponse:
        try:
            media_type = _media_type(response)
            if accept_media_types and media_type not in accept_media_types:
                raise UnsupportedMediaType(f"{final} served {media_type or 'no media type'}")
            content = self._read_body(response)
        finally:
            response.close()
        return FetchedResponse(
            url=final,
            requested_url=requested,
            status_code=response.status_code,
            headers=dict(response.headers),
            content=content,
            media_type=media_type,
            charset=response.charset_encoding,
            fetched_at=self._clock(),
            addresses=target.addresses,
            redirect_chain=chain,
        )

    def _read_body(self, response: httpx.Response) -> bytes:
        """Stream the body, abandoning it the moment it breaks a budget."""
        limits = self._limits
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limits.max_response_bytes:
            raise ResponseTooLarge(
                f"declared content-length {declared} exceeds {limits.max_response_bytes} bytes"
            )

        chunks: list[bytes] = []
        decoded = 0
        try:
            for chunk in response.iter_bytes():
                decoded += len(chunk)
                received = max(response.num_bytes_downloaded, 1)
                ratio = decoded / received
                if (
                    decoded >= limits.compression_ratio_floor_bytes
                    and ratio > limits.max_compression_ratio
                ):
                    raise CompressionBombDetected(
                        f"{decoded} bytes decoded from {received} received (ratio {ratio:.0f})"
                    )
                if decoded > limits.max_response_bytes:
                    raise ResponseTooLarge(f"body exceeds {limits.max_response_bytes} bytes")
                chunks.append(chunk)
        except httpx.TimeoutException as error:
            raise FetchTimeout(str(error)) from error
        except httpx.HTTPError as error:
            raise TransportFailure(str(error)) from error
        return b"".join(chunks)


def _canonicalise(url: str | CanonicalUrl, *, base: CanonicalUrl | None = None) -> CanonicalUrl:
    """Normalise a target, turning a refusal into the vocabulary the guard already uses."""
    if isinstance(url, CanonicalUrl) and base is None:
        return url
    try:
        return normalize_url(str(url), base=base)
    except UnsupportedScheme as error:
        raise SsrfBlocked(BlockReason.SCHEME, str(url), str(error)) from error
    except UnsafeUrl as error:
        raise FetchError(f"unusable target {url!r}: {error}") from error


def _redirect_location(response: httpx.Response) -> str | None:
    if response.status_code not in REDIRECT_STATUS_CODES:
        return None
    return response.headers.get("location") or None


def _media_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()
