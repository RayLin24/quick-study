from __future__ import annotations

import re
from pathlib import Path

from utils.call_llm import call_llm
from utils.errors import format_error

INDEX_FILENAMES = ("index.md", "README.md")
SOURCE_RE = re.compile(
    r"(?:\#\s*source:|\*source:|\*\s*source:)\s*`?([^\s*`]+)`?",
    re.IGNORECASE,
)


class AskRefused(ValueError):
    """No generated tutorial (or empty question)."""


def _tutorial_index(folder: Path) -> Path | None:
    for name in INDEX_FILENAMES:
        path = folder / name
        if path.is_file():
            return path
    return None


def collect_tutorial_bundle(folder: Path) -> dict:
    root = Path(folder)
    index = _tutorial_index(root)
    if index is None:
        raise AskRefused(format_error("还没有生成这篇教程，无法提问。请先生成教程。"))
    parts: list[str] = []
    sources: list[str] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        parts.append(f"## file: {path.name}\n{text}")
        sources.extend(SOURCE_RE.findall(text))
    unique_sources = sorted({item.strip() for item in sources if item.strip()})
    return {
        "markdown": "\n\n".join(parts),
        "sources": unique_sources,
        "files": [p.name for p in sorted(root.glob("*.md"))],
    }


def build_ask_prompt(bundle: dict, question: str) -> str:
    source_lines = "\n".join(f"- {path}" for path in bundle["sources"]) or "- （教程正文未标注 # source: 路径）"
    return f"""你是 Quick Study 的教程问答助手。只能根据下面已经生成的教程 Markdown，以及其中标注的源码路径回答。

规则：
- 不要使用教程以外的知识，不要检索、不要编造仓库里没有出现的文件。
- 可以引用教程章节标题，以及文中的 `# source:` / `*source:` 路径。
- 如果教程里没有写到，明确回答「教程里没有提到」，不要猜测。
- 用与问题相同的语言回答。

已标注的源码路径：
{source_lines}

教程全文：
{bundle["markdown"]}

问题：
{question}
"""


def ask_tutorial(folder: Path, question: str, *, call=None) -> str:
    q = (question or "").strip()
    if not q:
        raise AskRefused(format_error("请输入问题"))
    bundle = collect_tutorial_bundle(folder)
    prompt = build_ask_prompt(bundle, q)
    caller = call or call_llm
    return caller(prompt, use_cache=False, temperature=0.2)
