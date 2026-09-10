"""Provider-aware cost estimate. OpenRouter is priced per model, not flat."""

from __future__ import annotations

import json
import os
from pathlib import Path

# USD per 1M tokens. Conservative public list prices (2026-03).
# prompt / completion
PRICES = {
    "OPENROUTER": (0.15, 0.60),  # fallback only when the model is unknown
    "OPENAI": (2.50, 10.00),  # gpt-4o-class
    "ANTHROPIC": (3.00, 15.00),
    "GEMINI": (0.10, 0.40),
    "OLLAMA": (0.0, 0.0),
}

# OpenRouter model id → (prompt, completion) USD / 1M tokens.
# Default product model stays z-ai/glm-5.3-flash.
OPENROUTER_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "z-ai/glm-5.3-flash": (0.15, 0.60),
    "z-ai/glm-4.5-flash": (0.10, 0.40),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4.1": (2.00, 8.00),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "anthropic/claude-sonnet-4": (3.00, 15.00),
    "google/gemini-2.0-flash": (0.10, 0.40),
    "google/gemini-2.5-flash": (0.15, 0.60),
    "deepseek/deepseek-chat": (0.14, 0.28),
    "qwen/qwen-2.5-72b-instruct": (0.35, 0.40),
}

DEFAULT_OPENROUTER_MODEL = "z-ai/glm-5.3-flash"


def provider_name() -> str:
    return (os.getenv("LLM_PROVIDER") or "OPENROUTER").strip().upper()


def _normalize_model(model: str | None) -> str:
    return (model or "").strip()


def _load_override_table() -> dict[str, tuple[float, float]]:
    raw = (os.getenv("OPENROUTER_PRICES_JSON") or "").strip()
    if not raw:
        return {}
    try:
        if raw.startswith("{") or raw.startswith("["):
            payload = json.loads(raw)
        else:
            payload = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    table: dict[str, tuple[float, float]] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                table[str(key).strip()] = (float(value[0]), float(value[1]))
            elif isinstance(value, dict) and "prompt" in value:
                table[str(key).strip()] = (float(value["prompt"]), float(value.get("completion") or value["prompt"]))
    return table


def openrouter_model_name(model: str | None = None) -> str:
    return _normalize_model(model) or _normalize_model(os.getenv("OPENROUTER_MODEL")) or DEFAULT_OPENROUTER_MODEL


def openrouter_price(model: str | None = None) -> tuple[float, float]:
    name = openrouter_model_name(model)
    overrides = _load_override_table()
    if name in overrides:
        return overrides[name]
    if name in OPENROUTER_MODEL_PRICES:
        return OPENROUTER_MODEL_PRICES[name]
    short = name.split("/")[-1]
    for key, pair in {**OPENROUTER_MODEL_PRICES, **overrides}.items():
        if key == name or key.endswith("/" + short) or key.split("/")[-1] == short:
            return pair
    return PRICES["OPENROUTER"]


def current_models(provider: str | None = None) -> dict[str, str]:
    from utils.models import structure_model, write_model

    name = (provider or provider_name()).upper()
    if name == "OPENROUTER":
        default = openrouter_model_name()
    else:
        default = _normalize_model(os.getenv(f"{name}_MODEL")) or name.lower()
    return {
        "provider": name,
        "default": default,
        "structure": structure_model(default),
        "write": write_model(default),
    }


def price_pair(provider: str | None = None, model: str | None = None) -> tuple[float, float]:
    name = (provider or provider_name()).upper()
    if name == "OPENROUTER":
        return openrouter_price(model)
    return PRICES.get(name, PRICES["OPENROUTER"])


def estimate_cost(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    calls: int = 0,
) -> dict:
    name = (provider or provider_name()).upper()
    model_name = openrouter_model_name(model) if name == "OPENROUTER" else _normalize_model(model)
    prompt_p, completion_p = price_pair(name, model_name)
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    if total_tokens and prompt + completion == 0:
        prompt = int(total_tokens)
    usd = (prompt / 1_000_000) * prompt_p + (completion / 1_000_000) * completion_p
    return {
        "provider": name,
        "model": model_name or None,
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


def estimate_from_usage(usage: dict | None, provider: str | None = None, model: str | None = None) -> dict:
    usage = usage or {}
    chosen = model or usage.get("model")
    if not chosen:
        models = current_models(provider)
        chosen = models["default"]
    return estimate_cost(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        calls=int(usage.get("calls") or 0),
        provider=provider,
        model=chosen,
    )


def estimate_preview_calls(estimated_calls: int | dict, avg_tokens: int = 2500) -> dict:
    """Price precheck / outline remaining calls. Dual OpenRouter models are split."""
    models = current_models()
    if isinstance(estimated_calls, dict):
        structure_n = int(
            (estimated_calls.get("identify") or 0)
            + (estimated_calls.get("relationships") or 0)
            + (estimated_calls.get("order") or 0)
        )
        write_n = int(estimated_calls.get("write_chapters") or estimated_calls.get("remaining") or 0)
        total_n = int(estimated_calls.get("total") or (structure_n + write_n))
    else:
        write_n = max(0, int(estimated_calls or 0))
        structure_n = 0
        total_n = write_n

    def _chunk(n: int, model: str) -> dict:
        prompt = int(n * avg_tokens * 0.7)
        completion = int(n * avg_tokens * 0.3)
        return estimate_cost(
            prompt_tokens=prompt,
            completion_tokens=completion,
            calls=n,
            provider=models["provider"],
            model=model,
        )

    if models["provider"] == "OPENROUTER" and structure_n and (
        models["structure"] != models["write"] or isinstance(estimated_calls, dict)
    ):
        structure = _chunk(structure_n, models["structure"])
        write = _chunk(write_n, models["write"])
        usd = round(structure["usd"] + write["usd"], 6)
        out = {
            "provider": models["provider"],
            "model": models["write"],
            "structure_model": models["structure"],
            "write_model": models["write"],
            "prompt_tokens": structure["prompt_tokens"] + write["prompt_tokens"],
            "completion_tokens": structure["completion_tokens"] + write["completion_tokens"],
            "total_tokens": structure["total_tokens"] + write["total_tokens"],
            "calls": total_n,
            "usd": usd,
            "currency": "USD",
            "rate_prompt_per_m": write["rate_prompt_per_m"],
            "rate_completion_per_m": write["rate_completion_per_m"],
            "parts": {"structure": structure, "write": write},
            "note": "按 OpenRouter 模型标价估算（结构/写章可双模型），实际账单以供应商为准",
            "estimated": True,
        }
        return out

    out = _chunk(total_n, models["default"])
    out["estimated"] = True
    out["structure_model"] = models["structure"]
    out["write_model"] = models["write"]
    return out
