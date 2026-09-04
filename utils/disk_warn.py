from __future__ import annotations

from utils.health import disk_free_bytes

WARN_BYTES = 512 * 1024 * 1024


def temp_clone_warning(path=None, *, need_bytes: int = WARN_BYTES) -> str | None:
    free = disk_free_bytes(path)
    if free < need_bytes:
        return f"临时克隆可能空间不足：剩余 {free // (1024 * 1024)}MB，建议至少 {need_bytes // (1024 * 1024)}MB。"
    return None
