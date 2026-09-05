"""Pick the 5 most worth-teaching entry files (feature 37). No embeddings."""

from __future__ import annotations

from pathlib import Path

ENTRY_NAMES = {
    "main.py",
    "app.py",
    "index.js",
    "index.ts",
    "cli.py",
    "__init__.py",
    "mod.rs",
    "lib.rs",
    "main.go",
    "manage.py",
    "wsgi.py",
    "asgi.py",
}


def pick_entry_files(files: list, *, limit: int = 5) -> list[dict]:
    scored = []
    for item in files or []:
        path = item[0] if isinstance(item, (list, tuple)) else str(item)
        name = Path(path).name.lower()
        posix = path.replace("\\", "/").lower()
        score = 0
        if name in ENTRY_NAMES:
            score += 8
        if name in {"readme.md", "readme.rst"}:
            score += 5
        if posix.startswith("src/") or "/src/" in posix:
            score += 2
        if "test" in posix:
            score -= 6
        depth = posix.count("/")
        score += max(0, 3 - depth)
        scored.append({"path": path, "score": score})
    scored.sort(key=lambda item: (-item["score"], item["path"]))
    picked = [item for item in scored if item["score"] > 0][:limit]
    if len(picked) < limit:
        extras = [item for item in scored if item not in picked]
        picked.extend(extras[: limit - len(picked)])
    return picked[:limit]
