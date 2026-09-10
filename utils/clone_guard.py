"""SSRF guard for git clone / ALLOW_GIT_HOSTS extra hosts.

Rejects private, loopback, link-local, and metadata addresses — both as
literals and after DNS resolution — and non-git ports.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# https / ssh / git protocol. Custom ALLOW_GIT_HOSTS still must use these.
ALLOWED_GIT_PORTS = {22, 443, 9418}

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata",
}

# Public forges: skip DNS (tests / offline). Extra ALLOW_GIT_HOSTS still resolve.
KNOWN_PUBLIC_HOSTS = {
    "github.com",
    "www.github.com",
    "gitlab.com",
    "www.gitlab.com",
    "gitea.com",
    "www.gitea.com",
}


class CloneRefused(ValueError):
    """Clone URL is not safe to fetch."""


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def parse_ip_literal(host: str):
    text = (host or "").strip().strip("[]")
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    # Decimal / hex forms that urlparse may leave as hostname.
    try:
        if text.startswith("0x"):
            return ipaddress.ip_address(int(text, 16))
        if text.isdigit():
            return ipaddress.ip_address(int(text, 10))
    except (ValueError, OverflowError):
        return None
    return None


def host_is_blocked_literal(host: str) -> bool:
    name = (host or "").strip().lower().rstrip(".")
    if not name:
        return True
    if name in BLOCKED_HOSTS or name.endswith(".localhost"):
        return True
    ip = parse_ip_literal(name)
    if ip is not None:
        return _ip_blocked(ip)
    return False


def resolve_host_ips(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addrs = []
    for info in infos:
        addr = info[4][0]
        if addr not in addrs:
            addrs.append(addr)
    return addrs


def host_is_blocked(host: str, *, resolve: bool = True) -> bool:
    if host_is_blocked_literal(host):
        return True
    if not resolve:
        return False
    try:
        addrs = resolve_host_ips(host)
    except (socket.gaierror, OSError, UnicodeError):
        # Unresolvable extra host is not a safe clone target.
        return True
    if not addrs:
        return True
    for addr in addrs:
        ip = parse_ip_literal(addr)
        if ip is None or _ip_blocked(ip):
            return True
    return False


def git_port_for(scheme: str, port: int | None) -> int:
    if port is not None:
        return int(port)
    if scheme == "https":
        return 443
    if scheme == "ssh":
        return 22
    if scheme == "git":
        return 9418
    return -1


def assert_safe_clone_url(url: str, *, resolve: bool | None = None) -> str:
    text = (url or "").strip()
    if not text:
        raise CloneRefused("空的仓库 URL")
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"https", "ssh", "git"}:
        raise CloneRefused("只允许 https / ssh / git 协议")
    host = (parsed.hostname or "").lower()
    if not host:
        raise CloneRefused("仓库 URL 缺少主机名")
    port = git_port_for(scheme, parsed.port)
    if port not in ALLOWED_GIT_PORTS:
        raise CloneRefused(f"拒绝非 git 端口 {port}")
    do_resolve = (host not in KNOWN_PUBLIC_HOSTS) if resolve is None else resolve
    if host_is_blocked(host, resolve=do_resolve):
        raise CloneRefused("拒绝内网 / 链路本地 / 回环地址（含解析后）")
    return text
