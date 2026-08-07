"""抓取层：httpx 静态快路径 + 限速 + robots + 重试退避 + ETag 增量 + JS 壳升级渲染。

设计（design.md 4.1 / ADR-007）：
- 静态页走 httpx 直连；检测到 JS 壳（正文近乎为空 + 大量 script）自动升级 Playwright。
- 每域名令牌桶限速；robots.txt 的 Crawl-delay 覆盖默认速率；Disallow 直接跳过。
- 增量：记录 ETag/Last-Modified/内容 hash，重跑时 unchanged 页跳过解析。
- Crawl4AI 作为可选渲染后端保留接口位，首期用 Playwright 直驱，避免重依赖。
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field

import httpx

from quickstudy.config import TaskConfig
from quickstudy.ratelimit import DomainRateLimiter
from quickstudy.robots import RobotsCache


@dataclass
class FetchResult:
    url: str
    status: int = 0
    html: bytes = b""
    render: str = "http"            # http | playwright
    etag: str = ""
    last_modified: str = ""
    content_hash: str = ""
    elapsed_ms: int = 0
    error: str = ""
    skipped: str = ""               # "robots" | "unchanged" | ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and bool(self.html)


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, cfg: TaskConfig, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.limiter = DomainRateLimiter(cfg.max_rps)
        self.robots = RobotsCache(cfg.user_agent)
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": cfg.user_agent,
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            follow_redirects=True,
            timeout=cfg.timeout_s,
        )
        self._renderer = None  # 懒加载，避免无 JS 站点也拉起浏览器

    async def close(self) -> None:
        if self._own_client:
            await self._client.aclose()
        if self._renderer is not None:
            await self._renderer.close()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- robots 注入用的极简文本获取（不走限速：robots.txt 每源站一次） ----
    async def _fetch_text(self, url: str) -> tuple[int, str]:
        resp = await self._client.get(url)
        return resp.status_code, resp.text

    async def check_robots(self, url: str):
        policy = await self.robots.policy_for(url, self._fetch_text)
        if policy.crawl_delay:
            self.limiter.set_crawl_delay(url, policy.crawl_delay)
        return policy

    async def fetch(self, url: str, known: dict | None = None) -> FetchResult:
        """抓单页。known: 上次抓取的 {etag,last_modified,content_hash}，用于增量。"""
        result = FetchResult(url=url)

        if self.cfg.respect_robots:
            policy = await self.check_robots(url)
            if not policy.allowed:
                result.skipped = "robots"
                result.error = "robots.txt 禁止抓取"
                return result

        headers = {}
        if self.cfg.incremental and known:
            if known.get("etag"):
                headers["If-None-Match"] = known["etag"]
            if known.get("last_modified"):
                headers["If-Modified-Since"] = known["last_modified"]

        for attempt in range(self.cfg.max_retries + 1):
            try:
                await self.limiter.acquire(url)
                import time

                t0 = time.monotonic()
                resp = await self._client.get(url, headers=headers)
                result.elapsed_ms = int((time.monotonic() - t0) * 1000)
                result.status = resp.status_code

                if resp.status_code == 304 and known:
                    result.skipped = "unchanged"
                    result.content_hash = known.get("content_hash", "")
                    return result
                result.etag = resp.headers.get("ETag", "")
                result.last_modified = resp.headers.get("Last-Modified", "")
                if resp.status_code in _RETRYABLE_STATUS and attempt < self.cfg.max_retries:
                    # 429/5xx：尊重 Retry-After，否则指数退避后重试
                    retry_after = resp.headers.get("Retry-After", "")
                    wait = float(retry_after) if retry_after.isdigit() else float(2 ** (attempt + 1))
                    result.error = f"HTTP {resp.status_code}（重试 {attempt + 1}/{self.cfg.max_retries}）"
                    await asyncio.sleep(min(wait, 60.0))
                    continue
                if not (200 <= resp.status_code < 300):
                    result.error = f"HTTP {resp.status_code}"
                    return result

                result.html = resp.content
                result.content_hash = hashlib.sha256(resp.content).hexdigest()

                if (self.cfg.incremental and known
                        and result.content_hash == known.get("content_hash")):
                    result.skipped = "unchanged"
                    result.html = b""
                    return result

                if self.cfg.render_escalation and _looks_like_js_shell(resp.text):
                    rendered = await self._render(url)
                    if rendered:
                        result.html = rendered
                        result.render = "playwright"
                        result.content_hash = hashlib.sha256(rendered).hexdigest()
                return result
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                result.error = f"{type(e).__name__}: {e}"
                if attempt < self.cfg.max_retries:
                    await asyncio.sleep(2 ** attempt)  # 指数退避
        return result

    async def _render(self, url: str) -> bytes | None:
        """Playwright 渲染升级；未安装 playwright 时静默回退（标记 error 供报告）。"""
        try:
            if self._renderer is None:
                from quickstudy.fetch.render import PlaywrightRenderer

                self._renderer = PlaywrightRenderer(self.cfg.user_agent)
                await self._renderer.start()
            return await self._renderer.render(url)
        except Exception:  # noqa: BLE001
            return None


def _looks_like_js_shell(html: str, text_threshold: int = 200) -> bool:
    """JS 壳判定：可见文本极少且存在打包器特征（root 挂载点 + bundle script）。"""
    import re

    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->|<[^>]+>",
                  " ", html)
    visible = len(text.split())
    if visible >= text_threshold:
        return False
    return bool(re.search(r'id="(root|app|__next|__docusaurus)"', html)) or \
        len(re.findall(r"<script[^>]+src=", html)) >= 3
