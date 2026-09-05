"""Memory watermark + auto lower concurrency for large repos (feature 50)."""

from __future__ import annotations

import os
from pathlib import Path


def read_meminfo() -> dict:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return {"available_kb": None, "total_kb": None}
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        num = "".join(ch for ch in rest if ch.isdigit())
        if num:
            data[key] = int(num)
    return {
        "available_kb": data.get("MemAvailable") or data.get("MemFree"),
        "total_kb": data.get("MemTotal"),
    }


def suggest_concurrency(file_count: int, *, available_kb: int | None = None, current: int | None = None) -> dict:
    current = int(current if current is not None else (os.getenv("LLM_MAX_CONCURRENCY") or "5"))
    info = read_meminfo() if available_kb is None else {"available_kb": available_kb, "total_kb": None}
    avail = info.get("available_kb")
    suggested = current
    reason = "ok"
    if file_count >= 400:
        suggested = min(suggested, 2)
        reason = "many_files"
    if avail is not None and avail < 512 * 1024:
        suggested = min(suggested, 1)
        reason = "low_memory"
    elif avail is not None and avail < 1024 * 1024:
        suggested = min(suggested, 2)
        reason = "memory_watermark"
    suggested = max(1, suggested)
    return {
        "file_count": file_count,
        "available_kb": avail,
        "current": current,
        "suggested": suggested,
        "throttled": suggested < current,
        "reason": reason,
    }


def apply_concurrency(file_count: int) -> int:
    plan = suggest_concurrency(file_count)
    if plan["throttled"]:
        print(f"QUICK_STUDY_MEM: throttle concurrency {plan['current']} -> {plan['suggested']} ({plan['reason']})")
    return plan["suggested"]
