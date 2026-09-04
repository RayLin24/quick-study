from __future__ import annotations


def _names(items) -> set[str]:
    out = set()
    for item in items or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name:
            out.add(name)
    return out


def compare_abstractions(left, right) -> dict:
    a = _names(left)
    b = _names(right)
    return {
        "only_left": sorted(a - b),
        "only_right": sorted(b - a),
        "shared": sorted(a & b),
        "left_count": len(a),
        "right_count": len(b),
    }
