"""Who a request is allowed to reach.

The rule this module enforces is that a *name* is never trusted, only the addresses it
resolves to at the moment of use. Every lookup is re-checked, every answer in a record has
to clear the policy, and the caller is handed back the address it must actually connect to
so the value that was inspected is the value that gets dialled. Without that last step a
name can answer publicly for the check and privately for the connection, which is the
whole of DNS rebinding.

A blocked reason is always specific. "Blocked" alone cannot be reviewed by an operator
looking at why a documentation site failed to ingest.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.ingestion.web.urls import CanonicalUrl

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Resolves a host to textual addresses. Injected so tests never touch a real resolver.
Resolver = Callable[[str, int], Sequence[str]]


class BlockReason(StrEnum):
    """Why an address was refused. Persisted in step errors, so values are stable."""

    SCHEME = "scheme_not_allowed"
    PORT = "port_not_allowed"
    HOSTNAME = "hostname_not_allowed"
    LOOPBACK = "loopback_address"
    PRIVATE = "private_address"
    LINK_LOCAL = "link_local_address"
    CARRIER_GRADE_NAT = "carrier_grade_nat_address"
    UNIQUE_LOCAL = "unique_local_address"
    CLOUD_METADATA = "cloud_metadata_address"
    MULTICAST = "multicast_address"
    RESERVED = "reserved_address"
    UNSPECIFIED = "unspecified_address"
    NOT_GLOBAL = "not_globally_routable"
    NO_ADDRESS = "host_does_not_resolve"


class SsrfBlocked(Exception):
    """Raised instead of connecting. Carries enough detail to explain the refusal."""

    def __init__(self, reason: BlockReason, target: str, detail: str = "") -> None:
        self.reason = reason
        self.target = target
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"{reason.value} for {target}{suffix}")


#: Addresses whose only purpose is to serve credentials to whatever asks. They fall inside
#: link-local or private space, but naming them separately makes the refusal legible.
CLOUD_METADATA_ADDRESSES: Final[frozenset[str]] = frozenset(
    {
        "169.254.169.254",  # AWS, Azure, GCP, DigitalOcean, OpenStack
        "169.254.169.253",  # AWS VPC DNS
        "169.254.170.2",  # ECS task metadata
        "169.254.170.23",  # EKS Pod Identity
        "100.100.100.200",  # Alibaba Cloud
        "fd00:ec2::254",  # AWS IMDSv6
    }
)

#: Suffixes that only mean something inside somebody's network. Resolving them at all
#: would leak the deployment's search domains, so they are refused before any lookup.
PRIVATE_NAME_SUFFIXES: Final[tuple[str, ...]] = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".corp",
    ".home",
    ".home.arpa",
    ".onion",
    ".in-addr.arpa",
    ".ip6.arpa",
)
PRIVATE_NAMES: Final[frozenset[str]] = frozenset({"localhost"})

_CARRIER_GRADE_NAT: Final = ipaddress.ip_network("100.64.0.0/10")
_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")
_TEREDO: Final = ipaddress.ip_network("2001::/32")


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    """What the fetcher is permitted to reach. The defaults are the production values."""

    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    #: Only ever true for a test harness or an operator pointing at an intranet mirror.
    allow_private_addresses: bool = False
    #: A record with hundreds of answers is a resource-exhaustion vector, not a site.
    max_addresses: int = 8


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A cleared address, with the connection detail the fetcher must honour."""

    url: CanonicalUrl
    addresses: tuple[str, ...] = field(default=())

    @property
    def connect_url(self) -> CanonicalUrl:
        """The URL rewritten to the validated address.

        Connecting here while keeping ``url.host`` in the ``Host`` header and in the TLS
        handshake is what makes the check and the connection refer to the same machine.
        """
        return self.url.with_host(self.addresses[0])


def classify_address(value: str | IpAddress) -> BlockReason | None:
    """Return why ``value`` may not be contacted, or ``None`` when it is fine.

    Alternative notations are unwrapped first. ``::ffff:127.0.0.1``, ``64:ff9b::7f00:1``
    and ``2002:7f00:1::`` all reach 127.0.0.1, and a check that only looked at the literal
    text would wave every one of them through.
    """
    try:
        address = ipaddress.ip_address(value) if isinstance(value, str) else value
    except ValueError:
        return BlockReason.RESERVED

    if str(address) in CLOUD_METADATA_ADDRESSES:
        return BlockReason.CLOUD_METADATA

    embedded = _embedded_ipv4(address)
    if embedded is not None:
        return classify_address(embedded) or BlockReason.RESERVED
    if isinstance(address, ipaddress.IPv6Address) and address in _TEREDO:
        return BlockReason.RESERVED

    if address.is_unspecified:
        return BlockReason.UNSPECIFIED
    if address.is_loopback:
        return BlockReason.LOOPBACK
    if address.is_link_local:
        return BlockReason.LINK_LOCAL
    if address.is_multicast:
        return BlockReason.MULTICAST
    if isinstance(address, ipaddress.IPv4Address):
        if address in _CARRIER_GRADE_NAT:
            return BlockReason.CARRIER_GRADE_NAT
    elif address.is_site_local or _is_unique_local(address):
        return BlockReason.UNIQUE_LOCAL
    # Checked before ``is_private``: Python folds 240.0.0.0/4 and the broadcast address
    # into the private list, and "reserved" is the more accurate thing to tell an operator.
    if address.is_reserved:
        return BlockReason.RESERVED
    if address.is_private:
        return BlockReason.PRIVATE
    if not address.is_global:
        return BlockReason.NOT_GLOBAL
    return None


def classify_hostname(host: str) -> BlockReason | None:
    """Refuse names that can only resolve to something inside the deployment.

    A single-label name is included because it is completed with the resolver's search
    domain, which turns ``intranet`` into whatever the host network decides it means.
    """
    name = host.strip().rstrip(".").lower()
    if not name:
        return BlockReason.HOSTNAME
    if _is_ip_literal(name):
        return None
    if name in PRIVATE_NAMES or name.endswith(PRIVATE_NAME_SUFFIXES):
        return BlockReason.HOSTNAME
    if "." not in name:
        return BlockReason.HOSTNAME
    return None


def system_resolver(host: str, port: int) -> tuple[str, ...]:
    """Resolve through the operating system, preserving every answer in the record."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    ordered: dict[str, None] = {}
    for info in infos:
        ordered.setdefault(str(info[4][0]), None)
    return tuple(ordered)


class AddressGuard:
    """Decides whether a canonical URL may be fetched, and to which address.

    Stateless by design. Caching a verdict is the same mistake as trusting the name: the
    answer is only valid for the resolution it came from.
    """

    def __init__(
        self,
        *,
        policy: NetworkPolicy | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self._policy = policy or NetworkPolicy()
        self._resolve = resolver

    @property
    def policy(self) -> NetworkPolicy:
        return self._policy

    def check_url(self, url: CanonicalUrl) -> ResolvedTarget:
        """Clear ``url`` for one request, returning the addresses it may be sent to."""
        target = str(url)
        if url.scheme not in self._policy.allowed_schemes:
            raise SsrfBlocked(BlockReason.SCHEME, target, url.scheme)
        if url.port not in self._policy.allowed_ports:
            raise SsrfBlocked(BlockReason.PORT, target, str(url.port))

        if url.is_ip_literal:
            return ResolvedTarget(url, self._screen((url.host,), target))

        hostname_reason = classify_hostname(url.host)
        if hostname_reason is not None:
            raise SsrfBlocked(hostname_reason, target, url.host)
        return ResolvedTarget(url, self._screen(self._lookup(url, target), target))

    def _lookup(self, url: CanonicalUrl, target: str) -> Sequence[str]:
        try:
            answers = self._resolve(url.host, url.port)
        except OSError as error:
            raise SsrfBlocked(BlockReason.NO_ADDRESS, target, str(error)) from error
        if not answers:
            raise SsrfBlocked(BlockReason.NO_ADDRESS, target, url.host)
        return answers

    def _screen(self, answers: Iterable[str], target: str) -> tuple[str, str]:
        """Clear every answer, then keep at most ``max_addresses`` of them.

        Every answer has to pass, not just the one that would be used. A record that mixes
        a public and a private address is an attempt to win a race on a later resolution,
        so the name is refused as a whole.
        """
        cleared: list[str] = []
        for answer in answers:
            reason = classify_address(answer)
            if reason is not None and not self._policy.allow_private_addresses:
                raise SsrfBlocked(reason, target, answer)
            cleared.append(str(ipaddress.ip_address(answer)))
        if not cleared:
            raise SsrfBlocked(BlockReason.NO_ADDRESS, target)
        return tuple(cleared[: self._policy.max_addresses])


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_unique_local(address: ipaddress.IPv6Address) -> bool:
    return int(address) >> 121 == 0x7E  # fc00::/7


def _embedded_ipv4(address: IpAddress) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address an IPv6 notation actually reaches, when there is one."""
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    for candidate in (address.ipv4_mapped, address.sixtofour):
        if candidate is not None:
            return candidate
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None
