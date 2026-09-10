"""Seeded public showcase of star-repo tutorials (#40). No live regenerate."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GALLERY_DIR = ROOT / "gallery"
MANIFEST_NAME = "manifest.json"

# Curated, already-generated upstream examples — do not call the LLM.
DEFAULT_ITEMS = [
    {
        "name": "PocketFlow",
        "repo": "https://github.com/The-Pocket/PocketFlow",
        "stars_note": "100-line LLM framework",
        "local": "docs/PocketFlow",
        "pages": "./PocketFlow/",
    },
    {
        "name": "FastAPI",
        "repo": "https://github.com/tiangolo/fastapi",
        "stars_note": "high-star web framework",
        "local": "docs/FastAPI",
        "pages": "./FastAPI/",
    },
    {
        "name": "Flask",
        "repo": "https://github.com/pallets/flask",
        "stars_note": "classic Python web",
        "local": "docs/Flask",
        "pages": "./Flask/",
    },
    {
        "name": "Click",
        "repo": "https://github.com/pallets/click",
        "stars_note": "CLI decorator classic",
        "local": "docs/Click",
        "pages": "./Click/",
    },
    {
        "name": "Requests",
        "repo": "https://github.com/psf/requests",
        "stars_note": "HTTP for humans",
        "local": "docs/Requests",
        "pages": "./Requests/",
    },
]


def load_manifest(root: Path | None = None) -> list[dict]:
    base = Path(root or ROOT)
    path = base / "gallery" / MANIFEST_NAME
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            return items
    return list(DEFAULT_ITEMS)


def gallery_payload(root: Path | None = None) -> dict:
    base = Path(root or ROOT)
    items = []
    for raw in load_manifest(base):
        local = base / str(raw.get("local") or "")
        items.append(
            {
                **raw,
                "seeded": local.is_dir(),
                "chapter_count": len(list(local.glob("*.md"))) if local.is_dir() else 0,
            }
        )
    return {"title": "Quick Study showcase", "items": items, "regenerate_required": False}


def render_gallery_html(payload: dict | None = None) -> str:
    data = payload or gallery_payload()
    cards = []
    for item in data["items"]:
        cards.append(
            "<article class='panel'>"
            f"<h2>{item.get('name')}</h2>"
            f"<p>{item.get('stars_note') or ''}</p>"
            f"<p><a href='{item.get('repo')}'>repo</a>"
            f" · <a href='{item.get('pages')}'>Pages</a>"
            f" · seeded={item.get('seeded')} chapters={item.get('chapter_count')}</p>"
            "</article>"
        )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Quick Study Gallery</title>"
        "<link rel='stylesheet' href='/static/app.css'></head><body>"
        "<a class='skip-link' href='#gallery-main'>跳到展示廊</a>"
        "<header class='top'><a class='brand' href='/'>Quick Study</a>"
        "<p class='tag'>精选教程廊（种子，不现场再生成）</p></header>"
        f"<main id='gallery-main' class='layout'>{''.join(cards)}</main></body></html>"
    )
