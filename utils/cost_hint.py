from __future__ import annotations

COST_HINT_THRESHOLD = 12


def max_abstractions_cost_hint(n: int) -> str | None:
    try:
        value = int(n)
    except (TypeError, ValueError):
        return None
    if value <= COST_HINT_THRESHOLD:
        return None
    extra = value - 10
    return (
        f"max_abstractions={value} 会多写约 {extra} 章，"
        f"预估 LLM 调用增加 {extra + 1}–{extra + 3} 次（识别/关系/写章）。"
    )
