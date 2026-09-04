from __future__ import annotations


def learning_goal_block(goal: str | None) -> str:
    text = (goal or "").strip()
    if not text:
        return ""
    return (
        f"\nLEARNING GOAL (must constrain abstraction choice and chapter focus): {text}\n"
        "Prefer abstractions that help a reader reach this goal; drop unrelated modules.\n"
    )
