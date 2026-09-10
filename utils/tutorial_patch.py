"""PR guide → write change notes back into old chapters (#43)."""

from __future__ import annotations

import re
from pathlib import Path

from utils.pr_guide import parse_pr_diff
from utils.source_format import SOURCE_LINE_RE, split_source_ref

CHANGE_HEADING = "## 本次变更"


def _chapter_paths(folder: Path) -> list[Path]:
    return [
        path
        for path in sorted(Path(folder).glob("*.md"))
        if path.name not in {"glossary.md", "heatmap.md"}
    ]


def chapters_touching_files(folder: Path, changed_files: list[str]) -> list[str]:
    wanted = {str(p).replace("\\", "/").lstrip("./") for p in changed_files}
    hits = []
    for path in _chapter_paths(folder):
        text = path.read_text(encoding="utf-8")
        cited = []
        for match in SOURCE_LINE_RE.finditer(text):
            ref_path, _line = split_source_ref(match.group(1))
            cited.append(ref_path.replace("\\", "/"))
        if any(src in wanted or any(src.endswith(w) or w.endswith(src) for w in wanted) for src in cited):
            hits.append(path.name)
    return hits


def build_change_block(title: str, explanation: str, files: list[str]) -> str:
    listing = "\n".join(f"- `{name}`" for name in files[:20]) or "- (no files)"
    body = (explanation or "").strip() or "见本次 PR 导读。"
    return f"\n\n{CHANGE_HEADING}\n\n{title}\n\n{body}\n\n变更文件：\n{listing}\n"


def patch_chapters_from_pr(
    folder: Path,
    diff_text: str,
    *,
    title: str = "PR",
    explanation: str = "",
) -> dict:
    info = parse_pr_diff(diff_text)
    files = list(info.get("files") or [])
    targets = chapters_touching_files(folder, files)
    if not targets:
        # fallback: index + first chapter so MVP always writes something when a tutorial exists
        names = [p.name for p in _chapter_paths(folder)]
        targets = [n for n in names if n == "index.md"][:1] or names[:1]
    block = build_change_block(title, explanation, files)
    patched = []
    for name in targets:
        path = Path(folder) / name
        text = path.read_text(encoding="utf-8")
        if CHANGE_HEADING in text:
            text = re.sub(rf"\n*{re.escape(CHANGE_HEADING)}[\s\S]*$", "", text.rstrip())
        path.write_text(text.rstrip() + block, encoding="utf-8")
        patched.append(name)
    return {
        "patched": patched,
        "files": files,
        "file_count": info.get("file_count", len(files)),
        "heading": CHANGE_HEADING,
    }
