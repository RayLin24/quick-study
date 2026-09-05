from __future__ import annotations


def crawl_progress(stage: str, *, count: int | None = None, path: str | None = None) -> str:
    label = {
        "start": "CRAWL: listing files",
        "file": "CRAWL: read",
        "done": "CRAWL: done",
        "cache": "CRAWL: cache hit",
    }.get(stage, f"CRAWL: {stage}")
    bits = [label]
    if path:
        bits.append(path)
    if count is not None:
        bits.append(f"n={count}")
    return " ".join(bits)
