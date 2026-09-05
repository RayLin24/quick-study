"""Configurable IP / token rate buckets (feature 32)."""

from __future__ import annotations

import os
import threading
import time


class RateLimited(ValueError):
    """Caller exceeded the configured bucket."""


class BucketLimiter:
    def __init__(self, *, per_minute: int, burst: int | None = None):
        self.per_minute = max(1, int(per_minute))
        self.burst = max(self.per_minute, int(burst or per_minute))
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            times = [t for t in self._hits.get(key, []) if now - t < 60]
            if len(times) >= self.burst:
                raise RateLimited(f"速率限制：{key} 每分钟最多 {self.burst} 次")
            times.append(now)
            self._hits[key] = times


def limiter_from_env() -> BucketLimiter:
    rpm = int(os.getenv("RATE_LIMIT_PER_MINUTE") or "60")
    burst = int(os.getenv("RATE_LIMIT_BURST") or str(rpm))
    return BucketLimiter(per_minute=rpm, burst=burst)


def bucket_key(*, ip: str = "", token: str = "") -> str:
    if token:
        return f"token:{token[:8]}"
    return f"ip:{(ip or 'unknown')}"
