from __future__ import annotations

from pathlib import Path


def load_seed_texts(paths: list[str] | None, *, max_chars: int = 8000) -> str:
    chunks = []
    used = 0
    for raw in paths or []:
        path = Path(raw).expanduser()
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        remain = max_chars - used
        if remain <= 0:
            break
        piece = text[:remain]
        chunks.append(f"### seed: {path.name}\n{piece}")
        used += len(piece)
    return "\n\n".join(chunks)


def seed_prompt_block(paths: list[str] | None) -> str:
    body = load_seed_texts(paths)
    if not body:
        return ""
    return f"\nSeed files the author wants emphasized:\n{body}\n"
