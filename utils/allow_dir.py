from __future__ import annotations

import os
from pathlib import Path

from utils.errors import format_error


def allow_dir_root() -> Path | None:
    raw = (os.getenv("ALLOW_DIR_ROOT") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def assert_allowed_local_dir(path: Path) -> None:
    root = allow_dir_root()
    if root is None:
        return
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            format_error(f"本地目录不在 ALLOW_DIR_ROOT ({root}) 内：{resolved}")
        ) from exc
