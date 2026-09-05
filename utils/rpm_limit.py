from __future__ import annotations

import os
import threading
import time


class RateSemaphore:
    def __init__(self, rpm: int, concurrency: int):
        self.rpm = max(1, int(rpm))
        self._sem = threading.Semaphore(max(1, int(concurrency)))
        self._times: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        self._sem.acquire()
        while True:
            with self._lock:
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < 60]
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                wait = 60 - (now - self._times[0])
            time.sleep(max(0.05, wait))

    def release(self) -> None:
        self._sem.release()


def chapter_limiter() -> RateSemaphore:
    rpm = int(os.getenv("LLM_RPM") or os.getenv("LLM_MAX_CONCURRENCY") or "30")
    conc = int(os.getenv("LLM_MAX_CONCURRENCY") or "5")
    return RateSemaphore(rpm=max(conc, rpm), concurrency=conc)
