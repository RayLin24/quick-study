from __future__ import annotations

import argparse
from pathlib import Path

from utils.ask_tutorial import AskRefused, AskResult, ask_tutorial_detailed


def resolve_tutorial_folder(name: str, output: Path) -> Path:
    folder = Path(output) / name
    if folder.is_dir() and (folder / "index.md").is_file():
        return folder
    raise AskRefused(f"找不到教程 {name}")


def run_ask(tutorial: str, question: str, *, output: str | Path = "output") -> AskResult:
    folder = resolve_tutorial_folder(tutorial, Path(output))
    raw = ask_tutorial_detailed(folder, question)
    if isinstance(raw, AskResult):
        return raw
    return AskResult(answer=str(raw))


def main_ask(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quick-study ask", description="对已生成教程提问。")
    parser.add_argument("tutorial", help="output/ 下的教程目录名")
    parser.add_argument("question", help="问题")
    parser.add_argument("-o", "--output", default="output")
    args = parser.parse_args(argv)
    try:
        result = run_ask(args.tutorial, args.question, output=args.output)
    except AskRefused as exc:
        print(str(exc))
        return 2
    print(result.answer)
    if result.used_chapters:
        names = ", ".join(item.get("filename") or item.get("title") or "" for item in result.used_chapters)
        print(f"\nused_chapters: {names}")
    return 0
