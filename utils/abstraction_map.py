from __future__ import annotations

import json
from pathlib import Path


def build_abstraction_map(abstractions: list[dict], files: list) -> dict:
    mapping = []
    for item in abstractions or []:
        idxs = item.get("files") or []
        paths = []
        for idx in idxs:
            if isinstance(idx, int) and 0 <= idx < len(files):
                entry = files[idx]
                paths.append(entry[0] if isinstance(entry, (list, tuple)) else str(entry))
        mapping.append({"name": item.get("name"), "files": paths})
    return {"abstractions": mapping}


def write_abstraction_map(folder: Path, abstractions: list[dict], files: list) -> Path:
    dest = Path(folder) / "abstraction_map.json"
    dest.write_text(
        json.dumps(build_abstraction_map(abstractions, files), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dest
