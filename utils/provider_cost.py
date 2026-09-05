"""Provider-aware cost estimate (feature 7). Not just token counts."""

from __future__ import annotations

import os

# USD per 1M tokens. Conservative public list prices (2026-03).
# prompt / completion
PRICES = {
    "OPENROUTER": (0.15, 0.60),  # glm-5.3-flash ballpark
    "OPENAI": (2.50, 10.00),  # gpt-4o-class
    "ANTHROPIC": (3.00, 15.00),
    "GEMINI": (0.10, 0.40),
    "OLLAMA": (0.0, 0.0),
}


def provider_name() -> str:
    return (os.getenv("LLM_PROVIDER") or "OPENROUTER").strip().upper()


def price_pair(provider: str | None = None) -> tuple[float, float]:
    name = (provider or provider_name()).upper()
    return PRICES.get(name, PRICES["OPENROUTER"])


def estimate_cost(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    provider: str | None = None,
    calls: int = 0,
) -> dict:
    prompt_p, completion_p = price_pair(provider)
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    if total_tokens and prompt + completion == 0:
        prompt = int(total_tokens)
    usd = (prompt / 1_000_000) * prompt_p + (completion / 1_000_000) * completion_p
    name = (provider or provider_name()).upper()
    return {
        "provider": name,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion if prompt + completion else int(total_tokens or 0),
        "calls": int(calls or 0),
        "usd": round(usd, 6),
        "currency": "USD",
        "rate_prompt_per_m": prompt_p,
        "rate_completion_per_m": completion_p,
        "note": "按供应商公开标价估算，实际账单以供应商为准",
    }


def estimate_from_usage(usage: dict | None, provider: str | None = None) -> dict:
    usage = usage or {}
    return estimate_cost(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        calls=int(usage.get("calls") or 0),
        provider=provider,
    )


def estimate_preview_calls(estimated_calls: int, avg_tokens: int = 2500) -> dict:
    calls = max(0, int(estimated_calls or 0))
    prompt = int(calls * avg_tokens * 0.7)
    completion = int(calls * avg_tokens * 0.3)
    out = estimate_cost(prompt_tokens=prompt, completion_tokens=completion, calls=calls)
    out["estimated"] = True
    return out
