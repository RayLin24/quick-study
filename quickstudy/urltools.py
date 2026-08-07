"""URL 治理：归一化、分类、任务 ID。

归一化规则（design.md 4.1）：去 hash、去 utm 等跟踪参数、斜杠归一、host 小写。
分类规则：文档区收、blog/changelog/forum 弃、api-reference 标记由适配器决定去向（ADR-007）。
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|ref$|spm$|_ga$)", re.I)


class UrlClass(str, Enum):
    DOC = "doc"                  # 正文文档
    API_REFERENCE = "api"        # API 参考（去向由适配器策略决定）
    SKIP = "skip"                # 明确不抓（blog/changelog/forum 等）
    UNKNOWN = "unknown"          # 未识别，默认抓，报告中标注


# 路径模式 → 分类。匹配针对 path 的小写形式，按段匹配避免误伤。
# 注意：词目必须足够特异（"events" 会误伤 FastAPI /advanced/events 这类正文页）。
_SKIP_SEGMENTS = ("blog", "changelog", "release-notes", "releases", "forum",
                  "community", "news", "showcase", "sponsor")
_API_SEGMENTS = ("api-reference", "reference/api", "api/", "/api/")
_DOC_SEGMENTS = ("docs", "guide", "guides", "manual", "tutorial", "tutorials",
                 "documentation", "learn", "handbook", "reference", "features",
                 "advanced", "deployment", "howto")
# 非 HTML 资源：不作为页面抓取（PDF 按方案走 MinerU 专线，不在 M1 范围内）
_BINARY_EXT = (".zip", ".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".7z",
               ".rar", ".epub", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg",
               ".webp", ".mp4", ".mp3", ".ico", ".woff", ".woff2", ".whl")


def normalize_url(url: str, keep_query: bool = False) -> str:
    """归一化：协议/host 小写、去 fragment、去跟踪参数、去尾斜杠（根路径除外）。"""
    parts = urlparse(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/{2,}", "/", parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = ""
    if keep_query and parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if not _TRACKING_PARAMS.match(k)]
        query = urlencode(sorted(kept))
    return urlunparse((scheme, netloc, path, "", query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


def url_to_task_id(url: str) -> str:
    parts = urlparse(url)
    host = parts.netloc.replace(".", "-")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", parts.path).strip("-")[:40]
    return f"{host}-{slug}" if slug else host


def _path_segments(path: str) -> list[str]:
    return [s for s in path.lower().split("/") if s]


def classify_url(url: str) -> UrlClass:
    """按路径模式粗分类。适配器可用自己的规则覆盖（fingerprint 适配器优先）。"""
    path = urlparse(url).path.lower()
    if path.endswith(_BINARY_EXT):
        return UrlClass.SKIP
    segs = _path_segments(path)
    if any(seg in _SKIP_SEGMENTS for seg in segs):
        return UrlClass.SKIP
    for pat in _API_SEGMENTS:
        if pat.strip("/") in path:
            return UrlClass.API_REFERENCE
    if any(seg in _DOC_SEGMENTS for seg in segs):
        return UrlClass.DOC
    return UrlClass.UNKNOWN


def same_site(url: str, root: str) -> bool:
    """同域判断：允许根域名的子域（如 python.langchain.com 相对 langchain.com 不收）。"""
    a, b = urlparse(url).netloc.lower(), urlparse(root).netloc.lower()
    return a == b
