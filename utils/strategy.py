"""Named generation presets: beginner / deep / skim."""

from __future__ import annotations

STRATEGIES = {
    "beginner": {
        "id": "beginner",
        "label": "新手",
        "max_abstractions": 6,
        "max_size": 80000,
        "include": "src/** *.py *.md",
        "overview_only": False,
        "note": "少章、偏入口与主路径。",
    },
    "deep": {
        "id": "deep",
        "label": "深度",
        "max_abstractions": 12,
        "max_size": 150000,
        "include": "",
        "overview_only": False,
        "note": "更多抽象，单文件更大。",
    },
    "skim": {
        "id": "skim",
        "label": "速览",
        "max_abstractions": 5,
        "max_size": 50000,
        "include": "src/** *.py *.md README*",
        "overview_only": True,
        "note": "只出总览和关系图，不写长章。",
    },
}

INCLUDE_PRESETS = {
    "src": "src/**",
    "py": "*.py *.pyi",
    "web": "*.ts *.tsx *.js *.jsx",
    "docs": "*.md *.rst",
    "go": "*.go",
}


def get_strategy(name: str | None) -> dict:
    key = (name or "").strip().lower()
    if key in STRATEGIES:
        return dict(STRATEGIES[key])
    raise ValueError(f"未知策略: {name}（可选 beginner / deep / skim）")


def apply_strategy(payload: dict, name: str | None) -> dict:
    if not name:
        return payload
    preset = get_strategy(name)
    data = dict(payload)
    data["max_abstractions"] = preset["max_abstractions"]
    data["max_size"] = preset["max_size"]
    if preset.get("include") and not str(data.get("include") or "").strip():
        data["include"] = preset["include"]
    data["overview_only"] = bool(data.get("overview_only") or preset["overview_only"])
    data["strategy"] = preset["id"]
    return data
