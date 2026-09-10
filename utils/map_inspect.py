"""Visualize map_mode symbols / snippets that enter the prompt (#41)."""

from __future__ import annotations

from utils.map_slices import clip_map_mode_snippets, slice_file
from utils.repo_map import extract_symbol_locations


def inspect_map_slices(
    files: dict,
    *,
    max_total_chars: int = 24000,
    max_file_chars: int = 1800,
) -> dict:
    items = []
    for path, content in (files or {}).items():
        label = str(path).split("# ", 1)[1] if "# " in str(path) else str(path)
        text = content or ""
        symbols = extract_symbol_locations(label, text)
        snippet = slice_file(label, text, max_chars=max_file_chars)
        items.append(
            {
                "path": label,
                "symbols": symbols,
                "snippet": snippet,
                "chars": len(snippet),
                "source_chars": len(text),
            }
        )
    prompt = clip_map_mode_snippets(files or {}, max_total_chars=max_total_chars, max_file_chars=max_file_chars)
    return {
        "slices": items,
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "file_count": len(items),
        "symbol_count": sum(len(item["symbols"]) for item in items),
    }


def inspect_html(report: dict) -> str:
    rows = []
    for item in report.get("slices") or []:
        names = ", ".join(
            f"{s.get('name')}:L{s.get('line')}" for s in item.get("symbols") or []
        ) or "(no symbols)"
        snippet = (
            (item.get("snippet") or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        rows.append(
            f"<section><h2>{item.get('path')}</h2>"
            f"<p class='ask-hint'>{names} · {item.get('chars')} chars</p>"
            f"<pre class='log'>{snippet}</pre></section>"
        )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>map_mode inspect</title>"
        "<link rel='stylesheet' href='/static/app.css'></head><body>"
        "<a class='skip-link' href='#map-inspect'>跳到切片</a>"
        "<main id='map-inspect' class='layout'><section class='panel'>"
        f"<h1>map_mode 切片</h1><p>files={report.get('file_count')} "
        f"symbols={report.get('symbol_count')} prompt_chars={report.get('prompt_chars')}</p>"
        f"{''.join(rows)}</section></main></body></html>"
    )
