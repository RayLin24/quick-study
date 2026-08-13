"""An offline web server for the ingestion tests.

Requests are matched on the ``Host`` header rather than on the URL authority, because the
fetcher deliberately dials the IP address the SSRF guard cleared and carries the site name
in the header. Every request is recorded together with the address it was actually sent to,
which is what lets a test assert that an internal address was never contacted at all.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from typing import Final

import httpx

PUBLIC_IPS: Final[tuple[str, ...]] = (
    "93.184.216.34",
    "93.184.216.35",
    "93.184.216.36",
    "93.184.216.37",
)


@dataclass(frozen=True, slots=True)
class StubResponse:
    status_code: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "text/html; charset=utf-8"

    def to_httpx(self) -> httpx.Response:
        headers = {"content-type": self.content_type, **self.headers}
        return httpx.Response(self.status_code, content=self.body, headers=headers)


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    host_header: str
    target: str
    connected_address: str
    headers: httpx.Headers


class StubSite:
    """A tiny content-addressed web whose routes are ``(host, path?query)`` pairs."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], StubResponse] = {}
        self.requests: list[RecordedRequest] = []

    def add(
        self,
        url: str,
        body: bytes | str = b"",
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self._routes[_route_key(url)] = StubResponse(
            status_code=status_code,
            body=payload,
            headers=headers or {},
            content_type=content_type,
        )

    def add_redirect(self, url: str, location: str, *, status_code: int = 302) -> None:
        self._routes[_route_key(url)] = StubResponse(
            status_code=status_code, headers={"location": location}, content_type="text/plain"
        )

    def add_gzip(self, url: str, body: bytes, *, content_type: str = "text/html") -> None:
        self._routes[_route_key(url)] = StubResponse(
            body=gzip.compress(body),
            headers={"content-encoding": "gzip"},
            content_type=content_type,
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host_header = request.headers.get("host", request.url.netloc.decode())
        target = request.url.raw_path.decode()
        self.requests.append(
            RecordedRequest(
                method=request.method,
                host_header=host_header,
                target=target,
                connected_address=request.url.host,
                headers=request.headers,
            )
        )
        route = self._routes.get((host_header, target))
        if route is None:
            return httpx.Response(404, content=b"not found", headers={"content-type": "text/plain"})
        return route.to_httpx()

    def addresses_contacted(self) -> set[str]:
        return {record.connected_address for record in self.requests}

    def hosts_contacted(self) -> list[str]:
        return [record.host_header for record in self.requests]

    def targets_contacted(self) -> list[str]:
        return [record.target for record in self.requests]


def _route_key(url: str) -> tuple[str, str]:
    parsed = httpx.URL(url)
    host = parsed.netloc.decode()
    target = parsed.raw_path.decode()
    return host, target


def mapping_resolver(mapping: dict[str, tuple[str, ...]], default: tuple[str, ...] = ()):
    """Resolve names from a fixed table, so no test ever consults a real resolver."""

    def resolve(host: str, port: int) -> tuple[str, ...]:
        try:
            return mapping[host]
        except KeyError:
            if default:
                return default
            raise OSError(f"no address for {host}") from None

    return resolve


def public_resolver(*hosts: str):
    """Give every named host a distinct globally routable address."""
    table = {host: (PUBLIC_IPS[index % len(PUBLIC_IPS)],) for index, host in enumerate(hosts)}
    return mapping_resolver(table)
