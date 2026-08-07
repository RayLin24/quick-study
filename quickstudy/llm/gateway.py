"""LLM 网关（design.md 4.7 / ADR-006）：Anthropic 兼容协议（K3）。

- 统一入口：重试、限流、token 成本记账
- 结果缓存：按 (prompt_name, prompt_version, model, 输入) 的 hash 落盘 llm_cache/，
  断点续跑时命中缓存零成本；LLM 步骤非幂等，缓存即"可重放"的实现
- 溯源：每次调用记录 model/prompt_version/input_hash，随产物落盘
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    cache_key: str = ""


@dataclass
class CostLedger:
    """成本台账：按 prompt 类别聚合 token 消耗，预算熔断（design.md §9）。"""

    budget_usd: float = 0.0           # 0 = 不限
    price_in_per_m: float = 0.0       # 每百万 input token 单价（未知则只记账量）
    price_out_per_m: float = 0.0
    records: list[dict] = field(default_factory=list)

    def add(self, category: str, resp: LLMResponse) -> None:
        self.records.append({"category": category, "model": resp.model,
                             "in": resp.input_tokens, "out": resp.output_tokens,
                             "cached": resp.cached})

    @property
    def total_tokens(self) -> dict:
        return {"in": sum(r["in"] for r in self.records if not r["cached"]),
                "out": sum(r["out"] for r in self.records if not r["cached"])}

    def total_cost_usd(self) -> float:
        t = self.total_tokens
        return t["in"] / 1e6 * self.price_in_per_m + t["out"] / 1e6 * self.price_out_per_m

    def check_budget(self) -> None:
        if self.budget_usd > 0 and self.total_cost_usd() > self.budget_usd:
            raise RuntimeError(f"LLM 预算熔断: ${self.total_cost_usd():.2f} > ${self.budget_usd:.2f}")


class LLMGateway:
    """Anthropic Messages 协议客户端（K3）。同步接口为 asyncio 协程。"""

    def __init__(self, workspace: Path, model: str = "",
                 base_url: str = "", token: str = "",
                 max_concurrency: int = 4, ledger: CostLedger | None = None):
        self.model = model or os.environ.get("QUICKSTUDY_LLM_MODEL", "k3")
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL", "")).rstrip("/")
        self.token = token or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        if not self.base_url or not self.token:
            raise RuntimeError("LLM 网关未配置：需要 ANTHROPIC_BASE_URL 与 ANTHROPIC_AUTH_TOKEN")
        self.cache_dir = Path(workspace) / "llm_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or CostLedger()
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LLMGateway":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @staticmethod
    def cache_key(prompt_name: str, prompt_version: str, model: str,
                  system: str, user: str) -> str:
        raw = json.dumps([prompt_name, prompt_version, model, system, user],
                         ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    async def complete(self, prompt_name: str, prompt_version: str,
                       system: str, user: str, *,
                       max_tokens: int = 4096, temperature: float = 0.2,
                       use_cache: bool = True) -> LLMResponse:
        """单次补全。prompt_name+version 用于缓存键与成本归类——改 prompt 必须升版本号。"""
        key = self.cache_key(prompt_name, prompt_version, self.model, system, user)
        cache_file = self.cache_dir / f"{key}.json"
        if use_cache and cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            resp = LLMResponse(text=data["text"], model=data["model"],
                               input_tokens=data.get("input_tokens", 0),
                               output_tokens=data.get("output_tokens", 0),
                               cached=True, cache_key=key)
            self.ledger.add(prompt_name, resp)
            return resp

        self.ledger.check_budget()
        body = {"model": self.model, "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system, "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.token, "authorization": f"Bearer {self.token}",
                   "anthropic-version": "2023-06-01", "content-type": "application/json"}

        last_err = ""
        for attempt in range(4):
            try:
                async with self._sem:
                    resp = await self._client.post(f"{self.base_url}/v1/messages",
                                                   json=body, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {resp.status_code}"
                    await asyncio.sleep(min(2 ** (attempt + 1), 30))
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = "".join(b.get("text", "") for b in data.get("content", [])
                               if b.get("type") == "text")
                usage = data.get("usage", {})
                if data.get("stop_reason") == "max_tokens":
                    # 截断输出不缓存（思考型模型的 thinking 也吃 max_tokens），交由上层加预算重试
                    raise RuntimeError(
                        f"输出被 max_tokens={max_tokens} 截断（stop_reason=max_tokens）")
                out = LLMResponse(text=text, model=self.model,
                                  input_tokens=usage.get("input_tokens", 0),
                                  output_tokens=usage.get("output_tokens", 0),
                                  cache_key=key)
                cache_file.write_text(json.dumps({
                    "text": text, "model": self.model,
                    "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
                    "prompt_name": prompt_name, "prompt_version": prompt_version,
                }, ensure_ascii=False), encoding="utf-8")
                self.ledger.add(prompt_name, out)
                return out
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(min(2 ** (attempt + 1), 30))
        raise RuntimeError(f"LLM 调用失败（{prompt_name}@{prompt_version}）: {last_err}")


def extract_json(text: str) -> dict | list:
    """从模型输出中提取 JSON：容忍 ```json 围栏与前导/尾随文本。"""
    import re

    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = m.group(1) if m else text
    candidate = candidate.strip()
    # 找第一个 { 或 [ 到最后一个 } 或 ]
    start = min((i for i in (candidate.find("{"), candidate.find("[")) if i >= 0),
                default=-1)
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    if start < 0 or end <= start:
        raise ValueError(f"输出中未找到 JSON: {text[:200]}")
    return json.loads(candidate[start:end + 1])
