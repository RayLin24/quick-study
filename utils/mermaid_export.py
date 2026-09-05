from __future__ import annotations

import html
import re
from pathlib import Path

MERMAID_FENCE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


def extract_mermaid_blocks(text: str) -> list[str]:
    return [block.strip() for block in MERMAID_FENCE.findall(text or "") if block.strip()]


def mermaid_to_svg(source: str, *, title: str = "diagram") -> str:
    escaped = html.escape(source)
    width = 720
    height = 200 + min(400, source.count("\n") * 18)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{html.escape(title)}">'
        f'<rect width="100%" height="100%" fill="#FFF8EF"/>'
        f'<text x="16" y="28" font-size="14" font-family="monospace" fill="#1E3A5F">'
        f"{html.escape(title)}</text>"
        f'<foreignObject x="16" y="40" width="{width - 32}" height="{height - 56}">'
        f'<pre xmlns="http://www.w3.org/1999/xhtml" style="white-space:pre-wrap;font:13px monospace">'
        f"{escaped}</pre></foreignObject></svg>\n"
    )


def export_mermaid(folder: Path) -> dict:
    root = Path(folder)
    dest = root / "exports"
    dest.mkdir(parents=True, exist_ok=True)
    written = {"svg": [], "mmd": []}
    index = 0
    for path in sorted(root.glob("*.md")):
        for block in extract_mermaid_blocks(path.read_text(encoding="utf-8")):
            index += 1
            stem = f"{path.stem}_{index:02d}"
            mmd = dest / f"{stem}.mmd"
            svg = dest / f"{stem}.svg"
            mmd.write_text(block + "\n", encoding="utf-8")
            svg.write_text(mermaid_to_svg(block, title=stem), encoding="utf-8")
            written["mmd"].append(mmd.name)
            written["svg"].append(svg.name)
    return written
