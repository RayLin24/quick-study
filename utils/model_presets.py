"""Dual-model recommended presets + cost compare (#30). Defaults stay glm-5.3-flash."""

from __future__ import annotations

from utils.call_llm import DEFAULT_OPENROUTER_MODEL
from utils.provider_cost import estimate_cost, price_pair

# Recommended structure/write split. Product default remains a single flash model.
FLASH = DEFAULT_OPENROUTER_MODEL
# Stronger write is a suggestion only — not applied unless the operator sets env.
STRONGER_WRITE = "anthropic/claude-sonnet-4"


def recommended_presets() -> list[dict]:
    return [
        {
            "id": "flash",
            "label": "默认 · 双端 flash",
            "structure": FLASH,
            "write": FLASH,
            "default": True,
            "note": "产品默认。设置 LLM_PROVIDER=OPENROUTER，模型 z-ai/glm-5.3-flash。",
        },
        {
            "id": "dual",
            "label": "推荐双模型 · structure flash / write 更强",
            "structure": FLASH,
            "write": STRONGER_WRITE,
            "default": False,
            "note": "Identify/关系/排序用 flash；写章用更强模型。需自行设置 LLM_STRUCTURE_MODEL / LLM_WRITE_MODEL，不改默认。",
        },
    ]


def _stage_tokens(max_abstractions: int) -> dict[str, tuple[int, int]]:
    n = max(1, int(max_abstractions or 10))
    return {
        "structure": (int(3 * 1800 * 0.7), int(3 * 1800 * 0.3)),  # identify + rel + order
        "write": (int(n * 2500 * 0.7), int(n * 2500 * 0.3)),
    }


def _usd(prompt: int, completion: int, pair: tuple[float, float]) -> float:
    return (prompt / 1_000_000) * pair[0] + (completion / 1_000_000) * pair[1]


def compare_preset_costs(*, max_abstractions: int = 10, provider: str | None = None) -> list[dict]:
    """Compare flash-only vs dual-model estimated USD. Flash rates from OpenRouter table."""
    tokens = _stage_tokens(max_abstractions)
    flash_pair = price_pair(provider)
    # Stronger write: conservative public Sonnet-class list price (not billed unless chosen).
    strong_pair = (3.00, 15.00)
    rows = []
    for preset in recommended_presets():
        write_pair = flash_pair if preset["write"] == FLASH else strong_pair
        struct_usd = _usd(*tokens["structure"], flash_pair)
        write_usd = _usd(*tokens["write"], write_pair)
        base = estimate_cost(
            prompt_tokens=tokens["structure"][0] + tokens["write"][0],
            completion_tokens=tokens["structure"][1] + tokens["write"][1],
            calls=3 + max_abstractions,
            provider=provider,
        )
        rows.append(
            {
                **preset,
                "structure_usd": round(struct_usd, 6),
                "write_usd": round(write_usd, 6),
                "usd": round(struct_usd + write_usd, 6),
                "currency": "USD",
                "max_abstractions": max_abstractions,
                "estimated": True,
                "rate_note": base.get("note"),
            }
        )
    if len(rows) == 2:
        delta = rows[1]["usd"] - rows[0]["usd"]
        rows[1]["delta_vs_flash"] = round(delta, 6)
        rows[0]["delta_vs_flash"] = 0.0
    return rows
