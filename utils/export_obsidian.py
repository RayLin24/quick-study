"""Notion / Obsidian-friendly export (feature 24)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def build_obsidian_zip(folder: Path) -> bytes:
    root = Path(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        notes = []
        for path in sorted(root.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            wiki = text
            for other in root.glob("*.md"):
                wiki = wiki.replace(f"]({other.name})", f"]({other.stem})")
            zf.writestr(f"{root.name}/{path.stem}.md", wiki)
            notes.append(path.stem)
        moc = f"# {root.name}\n\n" + "\n".join(f"- [[{n}]]" for n in notes) + "\n"
        zf.writestr(f"{root.name}/_MOC.md", moc)
        zf.writestr(f"{root.name}/.obsidian/app.json", '{"legacyEditor":false,"livePreview":true}\n')
    return buf.getvalue()


def notion_index_markdown(folder: Path) -> str:
    root = Path(folder)
    lines = [f"# {root.name}", "", "Notion / 大纲导入用（每章一个子页标题）：", ""]
    for path in sorted(root.glob("*.md")):
        if path.name in {"glossary.md", "heatmap.md"}:
            continue
        lines.append(f"## {path.stem}")
        lines.append("")
        lines.append(path.read_text(encoding="utf-8"))
        lines.append("")
    return "\n".join(lines)
