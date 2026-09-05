from __future__ import annotations

import os
import sys

from utils.errors import format_error

LOOPBACK_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "0:0:0:0:0:0:0:1",
}


class BindRefused(RuntimeError):
    """Non-loopback listen without QUICK_STUDY_TOKEN."""


def is_loopback_host(host: str) -> bool:
    text = (host or "").strip().lower().strip("[]")
    if not text:
        return True
    if text in LOOPBACK_HOSTS:
        return True
    if text.startswith("127."):
        return True
    return False


PUBLIC_BIND_BANNER = """
================================================================================
拒绝启动：非回环地址未设置访问令牌
================================================================================
绑定 {host} 会把本机 Web（含 LLM 调用）暴露到公网。
未设置 QUICK_STUDY_TOKEN 时禁止 0.0.0.0 / 局域网 / 公网裸挂。

请任选其一：
  1) 本机访问：改用 --host 127.0.0.1
  2) 需要局域网/公网：先设置环境变量 QUICK_STUDY_TOKEN（强随机口令）
================================================================================
""".strip()


def public_bind_message(host: str) -> str:
    return format_error(PUBLIC_BIND_BANNER.format(host=host))


def assert_safe_bind(host: str, token: str | None = None) -> None:
    """Refuse to start on a public/non-loopback bind unless a token is set."""
    if is_loopback_host(host):
        return
    resolved = token if token is not None else os.getenv("QUICK_STUDY_TOKEN")
    if not (resolved or "").strip():
        raise BindRefused(public_bind_message(host))


def detect_uvicorn_host() -> str | None:
    """Best-effort: find the uvicorn Config.host on the call stack."""
    explicit = os.getenv("QUICK_STUDY_BIND")
    if explicit:
        return explicit
    try:
        from uvicorn.config import Config as UvicornConfig
    except ImportError:
        UvicornConfig = None
    frame = sys._getframe()
    while frame:
        loc = frame.f_locals
        candidates = [loc.get("config")]
        obj = loc.get("self")
        if obj is not None:
            candidates.append(getattr(obj, "config", None))
        for cfg in candidates:
            if UvicornConfig is not None and isinstance(cfg, UvicornConfig):
                host = getattr(cfg, "host", None)
                if isinstance(host, str) and host:
                    return host
        frame = frame.f_back
    return None


def token_from_headers(headers, cookies=None) -> str:
    auth = ""
    if headers is not None:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header_token = ""
    if headers is not None:
        header_token = headers.get("x-quick-study-token") or headers.get("X-Quick-Study-Token") or ""
    cookie_token = ""
    if cookies is not None:
        cookie_token = cookies.get("quick_study_token") or ""
    return (header_token or cookie_token or "").strip()
