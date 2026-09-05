"""Session / daily token circuit breaker (feature 6)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(ValueError):
    """Token budget would be exceeded."""


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def budget_path(output_dir: Path) -> Path:
    return Path(output_dir) / "budget.json"


def load_budget(output_dir: Path) -> dict:
    path = budget_path(output_dir)
    if not path.is_file():
        return {"session": 0, "daily": 0, "day": _day_key()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"session": 0, "daily": 0, "day": _day_key()}
    if data.get("day") != _day_key():
        data["daily"] = 0
        data["day"] = _day_key()
    data.setdefault("session", 0)
    data.setdefault("daily", 0)
    return data


def save_budget(output_dir: Path, data: dict) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    budget_path(output_dir).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def limits() -> dict:
    return {
        "session": int(os.getenv("TOKEN_BUDGET_SESSION") or "0"),
        "daily": int(os.getenv("TOKEN_BUDGET_DAILY") or "0"),
    }


def record_tokens(output_dir: Path, tokens: int) -> dict:
    data = load_budget(output_dir)
    add = max(0, int(tokens or 0))
    data["session"] = int(data.get("session") or 0) + add
    data["daily"] = int(data.get("daily") or 0) + add
    data["updated"] = int(time.time())
    save_budget(output_dir, data)
    return data


def assert_budget(output_dir: Path, upcoming: int = 0) -> dict:
    data = load_budget(output_dir)
    caps = limits()
    upcoming = max(0, int(upcoming or 0))
    for key, cap in caps.items():
        if cap > 0 and int(data.get(key) or 0) + upcoming > cap:
            raise BudgetExceeded(f"{key} token 预算已达上限 {cap}（已用 {data.get(key)}）")
    return {"used": data, "limits": caps}
