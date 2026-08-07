"""任务配置：默认值 ← 环境变量 ← YAML 配置文件 ← CLI 参数（优先级递增）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class TaskConfig:
    """一次生成任务的配置。M1 只使用抓取/解析相关字段，其余为后续里程碑预留。"""

    root_url: str
    workspace: Path = Path("workspace")
    task_id: str = ""

    # --- 发现 ---
    max_pages: int = 2000
    max_depth: int = 6
    include_prefixes: list[str] = field(default_factory=list)  # scope 收窄（ADR-003）
    exclude_prefixes: list[str] = field(default_factory=list)

    # --- 抓取 ---
    max_rps: float = 5.0
    timeout_s: float = 30.0
    max_retries: int = 3
    respect_robots: bool = True
    render_escalation: bool = True  # JS 壳自动升级 Playwright
    incremental: bool = False       # 基于 ETag/内容hash 只处理变化页
    user_agent: str = "quickstudy/0.1 (doc-crawler; polite)"

    # --- 解析 ---
    keep_api_reference: str = "appendix"  # keep | appendix | drop（适配器可覆盖）

    # --- LLM（M2 起用，M1 仅占位） ---
    llm_model: str = ""
    embed_model: str = ""

    @classmethod
    def load(cls, root_url: str, config_path: str | None = None, **cli_overrides) -> "TaskConfig":
        data: dict = {}
        if config_path:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        env_map = {
            "max_rps": ("QUICKSTUDY_MAX_RPS", float),
            "user_agent": ("QUICKSTUDY_USER_AGENT", str),
            "llm_model": ("QUICKSTUDY_LLM_MODEL", str),
            "embed_model": ("QUICKSTUDY_EMBED_MODEL", str),
        }
        for key, (env_name, cast) in env_map.items():
            if key not in data and os.environ.get(env_name):
                data[key] = cast(os.environ[env_name])

        # CLI 覆盖（None 表示未传）
        for key, value in cli_overrides.items():
            if value is not None:
                data[key] = value

        data["root_url"] = root_url
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if not cfg.task_id:
            from quickstudy.urltools import url_to_task_id

            cfg.task_id = url_to_task_id(root_url)
        cfg.workspace = Path(cfg.workspace)
        return cfg

    @property
    def task_dir(self) -> Path:
        return self.workspace / self.task_id
