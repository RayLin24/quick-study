"""自愈回路（design.md 4.5 / 6.3.2 / ADR-005 回归护栏）。

stderr + 完整代码 + 原始片段回喂 LLM → 完整修复文件 → 符号护栏校验 → 重跑。
最多 N 轮；护栏拒绝或轮次耗尽 → manual_todo（绝不带病入库）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from quickstudy.demo.generate import symbol_guard
from quickstudy.demo.sandbox import RunResult, run_demo
from quickstudy.knowledge.graph import _complete_json
from quickstudy.llm.gateway import LLMGateway
from quickstudy.llm import prompts

log = logging.getLogger(__name__)


def write_files(demo_dir: Path, files: list[dict]) -> None:
    for f in files:
        p = demo_dir / f["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["content"], encoding="utf-8", newline="\n")


def _all_code(demo: dict) -> str:
    return "\n\n".join(f"### {f['path']}\n{f['content']}" for f in demo["files"])


async def repair_loop(llm: LLMGateway, demo: dict, candidate: dict,
                      demo_dir: Path, target_symbols: list[str],
                      max_rounds: int = 3) -> tuple[RunResult, list[dict]]:
    """返回 (最终运行结果, 修复历史)。历史条目含每轮成败与护栏判定。"""
    history: list[dict] = []
    result = run_demo(demo_dir, demo["run_command"], tech="python")
    ok, reason = result.passed(demo.get("stdout_expect"))
    history.append({"round": 0, "ok": ok, "reason": reason,
                    "exit_code": result.exit_code, "duration_s": result.duration_s})

    for round_no in range(1, max_rounds + 1):
        if ok:
            break
        snippets = "\n\n".join(f"```\n{b['code']}\n```" for b in candidate["blocks"])
        user = (f"原始文档片段：\n{snippets[:4000]}\n\n"
                f"当前代码：\n{_all_code(demo)[:8000]}\n\n"
                f"验收判定结果：未通过——{reason}\n"
                f"（若原因是「输出缺少预期模式」，说明程序没有打印该证据字面值，"
                f"请补充对应打印或修正实现；不得删除检查代码。）\n\n"
                f"运行信息（exit={result.exit_code}）：\n"
                f"STDOUT:\n{result.stdout[-2000:]}\nSTDERR:\n{result.stderr[-3000:]}")
        try:
            fixed = await _complete_json(
                llm, "demo_fix", prompts.PROMPT_DEMO_FIX_VERSION,
                prompts.PROMPT_DEMO_FIX_SYSTEM, user, max_tokens=8192)
        except Exception as e:  # noqa: BLE001
            history.append({"round": round_no, "ok": False,
                            "reason": f"修复调用失败: {e}"})
            continue

        new_files = [{"path": str(f["path"]), "content": str(f["content"])}
                     for f in fixed.get("files", []) if f.get("path") and f.get("content")]
        if not new_files:
            history.append({"round": round_no, "ok": False, "reason": "修复结果无文件"})
            continue

        # 回归护栏（ADR-005）：目标符号保留不足 → 拒绝本次修复，记一轮失败
        guard_ok, missing = symbol_guard(target_symbols,
                                         "\n".join(f["content"] for f in new_files))
        if not guard_ok:
            history.append({"round": round_no, "ok": False,
                            "reason": f"护栏拒绝：目标符号丢失 {missing}"})
            continue

        demo["files"] = new_files
        write_files(demo_dir, new_files)
        result = run_demo(demo_dir, demo["run_command"], tech="python")
        ok, reason = result.passed(demo.get("stdout_expect"))
        history.append({"round": round_no, "ok": ok, "reason": reason,
                        "fix_note": str(fixed.get("fix_note", "")),
                        "exit_code": result.exit_code, "duration_s": result.duration_s})

    return result, history
