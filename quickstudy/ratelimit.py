"""按域名令牌桶限速（design.md 4.1：默认 5 req/s 可配，礼貌爬取）。"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse


class DomainRateLimiter:
    """每域名独立令牌桶；支持 robots.txt 的 Crawl-delay 覆盖默认速率。"""

    def __init__(self, max_rps: float = 5.0):
        self.default_interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._intervals: dict[str, float] = {}
        self._next_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def set_crawl_delay(self, url: str, delay_s: float | None) -> None:
        if delay_s and delay_s > 0:
            domain = urlparse(url).netloc.lower()
            # Crawl-delay 是"两次请求间隔秒数"，与令牌桶间隔同义；取更保守者
            self._intervals[domain] = max(delay_s, self.default_interval)

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def acquire(self, url: str) -> None:
        domain = self._domain(url)
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            interval = self._intervals.get(domain, self.default_interval)
            now = time.monotonic()
            wait = self._next_at.get(domain, 0.0) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_at[domain] = max(now, self._next_at.get(domain, 0.0)) + interval
