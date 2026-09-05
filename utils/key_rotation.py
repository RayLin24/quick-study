"""Key-rotation guidance + detection (feature 33). Does not change business logic."""

from __future__ import annotations

import os
from pathlib import Path

ROTATION_HINTS = (
    "把旧密钥当作已泄露：先在供应商控制台作废，再写入新值。",
    "只改环境变量 / .env，不要把密钥写进仓库或命令行。",
    "轮换后重启 web / worker，确认 /healthz 的 has_llm_key=true。",
    "若用过 QUICK_STUDY_TOKEN / READ_TOKEN，一并轮换并清掉旧 cookie。",
)


def detect_key_files(root: Path | None = None) -> dict:
    root = Path(root or ".")
    suspects = []
    for name in (".env", ".env.local"):
        path = root / name
        if path.is_file() and path.stat().st_size > 0:
            suspects.append(str(path))
    env_keys = [k for k in os.environ if k.endswith("_API_KEY") or k in {"GITHUB_TOKEN", "QUICK_STUDY_TOKEN"}]
    return {
        "env_files": suspects,
        "env_keys_present": sorted(set(env_keys)),
        "hints": list(ROTATION_HINTS),
        "ok": True,
    }
