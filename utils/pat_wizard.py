from __future__ import annotations

import re

TOKEN_RE = re.compile(r"^(ghp_|github_pat_|glpat-|gitea_)[A-Za-z0-9_]{8,}$")


def classify_pat(token: str) -> dict:
    raw = (token or "").strip()
    if not raw:
        return {"ok": False, "kind": None, "hint": "先到 GitHub Settings → Developer settings → Fine-grained token 创建，只勾 repo 读权限。"}
    if TOKEN_RE.match(raw):
        kind = "github" if raw.startswith(("ghp_", "github_pat_")) else ("gitlab" if raw.startswith("glpat-") else "gitea")
        return {"ok": True, "kind": kind, "hint": "令牌格式看起来正确。请写入 GITHUB_TOKEN 环境变量，不要贴到命令行。"}
    if len(raw) < 8:
        return {"ok": False, "kind": None, "hint": "令牌太短。"}
    return {"ok": False, "kind": None, "hint": "未识别前缀。GitHub PAT 通常以 ghp_ 或 github_pat_ 开头。"}


def wizard_steps() -> list[str]:
    return [
        "打开 GitHub → Settings → Developer settings → Personal access tokens",
        "创建 Fine-grained token，仓库权限选 Contents: Read",
        "复制令牌到本机 .env 的 GITHUB_TOKEN=，不要写进 git",
        "不要使用 -t / --token 命令行参数",
        "回到本页点「检查格式」确认前缀",
    ]
