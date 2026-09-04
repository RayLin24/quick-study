from __future__ import annotations

import os
import shutil
from pathlib import Path

from web.bind import is_loopback_host


def has_llm_key() -> bool:
    provider = (os.getenv("LLM_PROVIDER") or "OPENROUTER").strip().upper()
    if provider == "GEMINI":
        return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_PROJECT_ID") or "").strip())
    key_var = f"{provider}_API_KEY"
    return bool((os.getenv(key_var) or os.getenv("OPENROUTER_API_KEY") or "").strip())


def disk_free_bytes(path: str | Path | None = None) -> int:
    target = Path(path or os.getenv("QUICK_STUDY_OUTPUT") or "output")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        target = Path(".")
    usage = shutil.disk_usage(str(target))
    return int(usage.free)


def health_payload(*, bind_host: str | None = None, output_dir: str | Path | None = None) -> dict:
    host = bind_host if bind_host is not None else (os.getenv("QUICK_STUDY_BIND") or "127.0.0.1")
    free = disk_free_bytes(output_dir)
    return {
        "ok": True,
        "disk_free_bytes": free,
        "disk_free_mb": round(free / (1024 * 1024), 1),
        "has_llm_key": has_llm_key(),
        "loopback": is_loopback_host(host),
        "bind_host": host,
        "llm_timeout_seconds": float(os.getenv("LLM_TIMEOUT_SECONDS") or 300),
        "read_token_enabled": bool((os.getenv("QUICK_STUDY_READ_TOKEN") or "").strip()),
    }
