"""注释后置（design.md 4.5）：先跑通裸代码，再加逐行中文注释，注释后复跑确认。

注释只许加注释——通过符号/行级校验 + 沙箱复跑双保险。
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

from quickstudy.demo.repair import write_files
from quickstudy.demo.sandbox import run_demo
from quickstudy.knowledge.graph import _complete_json
from quickstudy.llm.gateway import LLMGateway
from quickstudy.llm import prompts

log = logging.getLogger(__name__)


def _code_skeleton(code: str) -> str:
    """AST 骨架：注释天然不可见；裸字符串表达式语句（docstring 等）移除。

    允许注释/文档字符串变化，拒绝任何真实代码改动（比按行剥离严谨：
    字符串里的 # 不会被误当注释，重排行/改变量名必被发现）。
    """
    tree = ast.parse(code)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            node.body = [s for s in body
                         if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                 and isinstance(s.value.value, str))] or [ast.Pass()]
    return ast.dump(tree)


def annotation_is_pure(before: str, after: str) -> bool:
    """校验注释/docstring 之外代码完全一致（防"加注释加挂了代码"）。"""
    try:
        return _code_skeleton(before) == _code_skeleton(after)
    except SyntaxError:
        return False


async def annotate_demo(llm: LLMGateway, demo: dict, demo_dir: Path) -> dict:
    """加注释 + README；校验失败则保留裸代码版本（注释是增强不是门槛）。返回 README 文本。"""
    before = {f["path"]: f["content"] for f in demo["files"]}
    code_text = "\n\n".join(f"### {p}\n{c}" for p, c in before.items())
    user = (f"Demo 名称：{demo['name']}\n运行方式：{demo['run_command']}\n\n"
            f"代码：\n{code_text[:10000]}")
    try:
        data = await _complete_json(
            llm, "demo_annotate", prompts.PROMPT_DEMO_ANNOTATE_VERSION,
            prompts.PROMPT_DEMO_ANNOTATE_SYSTEM, user, max_tokens=8192)
    except Exception as e:  # noqa: BLE001
        log.warning("注释生成失败，保留裸代码: %s", e)
        return {"readme": "", "annotated": False}

    new_files = [{"path": str(f["path"]), "content": str(f["content"])}
                 for f in data.get("files", []) if f.get("path") and f.get("content")]
    annotated_map = {f["path"]: f["content"] for f in new_files}

    # 纯注释校验：所有原文件的有效代码行必须逐行一致
    pure = all(p in annotated_map and annotation_is_pure(before[p], annotated_map[p])
               for p in before)
    if not pure:
        log.warning("注释改动超出了注释范围（%s），保留裸代码", demo["name"])
        return {"readme": str(data.get("readme", "")), "annotated": False}

    write_files(demo_dir, new_files)
    demo["files"] = new_files

    # 复跑确认（诚实性校验）
    result = run_demo(demo_dir, demo["run_command"], tech="python")
    ok, reason = result.passed(demo.get("stdout_expect"))
    if not ok:
        log.warning("注释后复跑失败（%s），回退为裸代码", reason)
        write_files(demo_dir, [{"path": p, "content": c} for p, c in before.items()])
        demo["files"] = [{"path": p, "content": c} for p, c in before.items()]
        return {"readme": str(data.get("readme", "")), "annotated": False}

    return {"readme": str(data.get("readme", "")), "annotated": True}
