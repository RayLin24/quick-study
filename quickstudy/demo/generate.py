"""Demo 补全生成 + 独立断言规格（ADR-005：断言与生成解耦）。

顺序有讲究：先由"验收员"调用只看原文片段产出行为要求清单，
再把清单注入"生成者"调用——生成者不知道断言如何被检查，验收者不看见实现。
"""
from __future__ import annotations

import re

from quickstudy.knowledge.graph import _complete_json
from quickstudy.llm.gateway import LLMGateway
from quickstudy.llm import prompts

_SYMBOL_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]{2,}\b|@(?:app|router)\.\w+")


def extract_target_symbols(snippets: list[str]) -> list[str]:
    """从原始片段提取目标 API 符号（自愈回归护栏的锚点）。"""
    found: set[str] = set()
    for code in snippets:
        found.update(_SYMBOL_RE.findall(code))
    # 去掉过于通用的词，保留框架特征符号
    return sorted(s for s in found if s not in {"True", "False", "None"})


def symbol_guard(original_symbols: list[str], new_code: str,
                 min_keep_ratio: float = 0.8) -> tuple[bool, list[str]]:
    """回归护栏：修复后代码必须保留 ≥80% 原始目标符号（防"删掉出错部分来修好"）。"""
    if not original_symbols:
        return True, []
    missing = [s for s in original_symbols if s not in new_code]
    keep_ratio = 1 - len(missing) / len(original_symbols)
    return keep_ratio >= min_keep_ratio, missing


async def build_assertion_spec(llm: LLMGateway, candidate: dict,
                               page_excerpt: str) -> dict:
    """独立验收规格：输入只有原文片段与上下文，看不见任何实现。"""
    snippets_text = "\n\n---\n\n".join(
        f"```{b['language']}\n{b['code']}\n```" for b in candidate["blocks"])
    user = (f"文档页：{candidate['title']}（{candidate['url']}）\n"
            f"章节：{candidate['section_path']}\n\n"
            f"上下文节选：\n{page_excerpt[:1500]}\n\n"
            f"代码片段：\n{snippets_text[:6000]}")
    data = await _complete_json(llm, "demo_spec", prompts.PROMPT_DEMO_SPEC_VERSION,
                                prompts.PROMPT_DEMO_SPEC_SYSTEM, user, max_tokens=4096)
    reqs = [str(r) for r in data.get("requirements", [])][:8]
    # 证据字面值是软建议（注入生成 prompt），不做硬匹配——硬门禁只有哨兵串
    evidence = [str(s) for s in data.get("evidence_strings", [])][:3]
    return {"requirements": reqs, "evidence_strings": evidence}


async def generate_demo(llm: LLMGateway, candidate: dict, page_excerpt: str,
                        spec: dict) -> dict:
    """LLM 补全完整 Demo。版本锚定 + 极简约束 + 断言清单注入。"""
    snippets_text = "\n\n---\n\n".join(
        f"```{b['language']}\n{b['code']}\n```" for b in candidate["blocks"])
    requirements = "\n".join(f"- {r}" for r in spec["requirements"])
    evidence = "\n".join(f"- {s}" for s in spec.get("evidence_strings", []))
    user = (f"文档站点：{candidate['url']}\n"
            f"文档版本：{candidate.get('version') or 'current（按所选技术当前稳定版 API 编写）'}\n"
            f"章节：{candidate['section_path']}\n\n"
            f"原始代码片段：\n{snippets_text[:6000]}\n\n"
            f"上下文节选：\n{page_excerpt[:2000]}\n\n"
            f"行为要求清单（独立验收依据，每条都必须有检查代码）：\n{requirements}\n\n"
            f"证据打印建议（尽量把这些字面值打印出来）：\n{evidence}")
    data = await _complete_json(llm, "demo_build", prompts.PROMPT_DEMO_BUILD_VERSION,
                                prompts.PROMPT_DEMO_BUILD_SYSTEM, user, max_tokens=8192)
    files = [{"path": str(f["path"]), "content": str(f["content"])}
             for f in data.get("files", []) if f.get("path") and f.get("content")]
    if not files:
        raise ValueError("生成结果无文件")
    stdout_expect = [str(s) for s in data.get("stdout_expect", [])]
    if "ALL CHECKS PASSED" not in stdout_expect:
        stdout_expect.append("ALL CHECKS PASSED")  # 哨兵串硬门禁
    return {"name": re.sub(r"[^a-z0-9-]+", "-", str(data.get("name", "demo")).lower()).strip("-") or "demo",
            "language": str(data.get("language", "python")),
            "files": files,
            "run_command": str(data.get("run_command", "python main.py")),
            "stdout_expect": stdout_expect,
            "notes": str(data.get("notes", ""))}
