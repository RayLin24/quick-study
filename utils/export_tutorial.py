from __future__ import annotations

import io
import zipfile
from pathlib import Path

from utils.ask_tutorial import HEADING_RE


def first_paragraph(text: str) -> str:
    lines = []
    started = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("<!--") or stripped.startswith(">"):
            continue
        if not stripped:
            if started:
                break
            continue
        started = True
        lines.append(stripped)
        if len(" ".join(lines)) > 220:
            break
    summary = " ".join(lines).strip()
    return summary[:240]


def build_llms_txt(folder: Path) -> str:
    root = Path(folder)
    rows = ["# Quick Study llms.txt", ""]
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        heading = HEADING_RE.search(text)
        title = heading.group(1).strip() if heading else path.stem
        summary = first_paragraph(text) or "（无摘要）"
        rows.append(f"## {title}")
        rows.append(f"path: {path.name}")
        rows.append(summary)
        rows.append("")
    return "\n".join(rows).rstrip() + "\n"


def zip_tutorial(folder: Path) -> bytes:
    root = Path(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith(".") and path.name not in {"meta.json"}:
                continue
            zf.write(path, arcname=path.relative_to(root).as_posix())
    return buf.getvalue()
