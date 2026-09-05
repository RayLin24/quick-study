"""Compare two crawled / tutorial repos as a reading guide (feature 11)."""

from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import SOURCE_RE, collect_tutorial_bundle


def _sources_from_tutorial(folder: Path) -> set[str]:
    try:
        bundle = collect_tutorial_bundle(folder)
    except Exception:
        return set()
    return {src.split("#", 1)[0] for src in bundle.get("sources") or []}


def _titles(folder: Path) -> set[str]:
    try:
        bundle = collect_tutorial_bundle(folder)
    except Exception:
        return set()
    return {ch.title for ch in bundle["chapters"] if ch.filename not in {"index.md", "README.md"}}


def compare_repos(left_folder: Path, right_folder: Path, *, left_name: str = "A", right_name: str = "B") -> dict:
    left_src = _sources_from_tutorial(left_folder)
    right_src = _sources_from_tutorial(right_folder)
    left_titles = _titles(left_folder)
    right_titles = _titles(right_folder)
    only_left = sorted(left_src - right_src)
    only_right = sorted(right_src - left_src)
    shared_src = sorted(left_src & right_src)
    markdown = [
        f"# {left_name} vs {right_name}",
        "",
        f"- 仅 {left_name} 的源文件：{len(only_left)}",
        f"- 仅 {right_name} 的源文件：{len(only_right)}",
        f"- 共有源文件：{len(shared_src)}",
        "",
        "## 建议先读",
        "",
    ]
    for path in (only_left + only_right)[:8]:
        side = left_name if path in only_left else right_name
        markdown.append(f"- `{path}`（只在 {side}）")
    if not only_left and not only_right:
        markdown.append("- 两边源文件集合接近，可按共有章对照阅读。")
    return {
        "left": left_name,
        "right": right_name,
        "only_left_sources": only_left,
        "only_right_sources": only_right,
        "shared_sources": shared_src,
        "only_left_titles": sorted(left_titles - right_titles),
        "only_right_titles": sorted(right_titles - left_titles),
        "shared_titles": sorted(left_titles & right_titles),
        "markdown": "\n".join(markdown) + "\n",
    }
