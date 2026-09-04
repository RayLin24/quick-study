from __future__ import annotations

from collections import Counter
from pathlib import Path


def suggest_include_patterns(files) -> str:
    """Copyable include suggestion when a crawl is over the file threshold."""
    paths = []
    if isinstance(files, dict):
        paths = list(files.keys())
    else:
        for item in files or []:
            if isinstance(item, (tuple, list)) and item:
                paths.append(item[0])
            else:
                paths.append(str(item))
    exts = Counter()
    tops = Counter()
    for path in paths:
        posix = str(path).replace("\\", "/")
        suffix = Path(posix).suffix
        if suffix:
            exts[suffix] += 1
        head = posix.split("/", 1)[0]
        if head and head not in {".", ".."}:
            tops[head] += 1
    bits = []
    if "src" in tops:
        bits.append("src/**")
    for ext, _n in exts.most_common(3):
        bits.append(f"*{ext}")
    if not bits:
        bits = ["src/**", "*.py"]
    # de-dupe preserve order
    seen = []
    for item in bits:
        if item not in seen:
            seen.append(item)
    return " ".join(seen)
