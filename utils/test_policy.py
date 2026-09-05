"""Visualize test-file chapter / default-exclude policy (feature 38)."""

from __future__ import annotations

from pathlib import Path

from utils.patterns import DEFAULT_EXCLUDE_PATTERNS, file_matches_any


def classify_files(files: list[str], *, exclude=None, tests_as_chapter: bool = False) -> dict:
    exclude = set(exclude or DEFAULT_EXCLUDE_PATTERNS)
    rows = []
    for path in files:
        posix = path.replace("\\", "/")
        is_test = file_matches_any(posix, {"*test*", "*tests/*", "**/test_*.py"})
        excluded = file_matches_any(posix, exclude)
        if is_test and tests_as_chapter:
            fate = "chapter"
        elif excluded or is_test:
            fate = "exclude"
        else:
            fate = "include"
        rows.append({"path": path, "test": is_test, "fate": fate})
    return {
        "tests_as_chapter": tests_as_chapter,
        "default_exclude": sorted(DEFAULT_EXCLUDE_PATTERNS),
        "files": rows,
        "counts": {
            "include": sum(1 for r in rows if r["fate"] == "include"),
            "exclude": sum(1 for r in rows if r["fate"] == "exclude"),
            "chapter": sum(1 for r in rows if r["fate"] == "chapter"),
        },
    }


def policy_markdown(report: dict) -> str:
    lines = [
        "# 测试文件策略",
        "",
        f"- 测试单独成章：{'是' if report.get('tests_as_chapter') else '否（默认排除）'}",
        f"- include {report['counts']['include']} / exclude {report['counts']['exclude']} / chapter {report['counts']['chapter']}",
        "",
    ]
    for row in report.get("files") or []:
        mark = {"include": "✓", "exclude": "×", "chapter": "章"}[row["fate"]]
        lines.append(f"- {mark} `{row['path']}`")
    return "\n".join(lines) + "\n"
