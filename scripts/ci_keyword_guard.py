#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.keyword_guard import check_manual


def main() -> int:
    result = check_manual(ROOT / "项目说明书.md")
    if not result["ok"]:
        print("说明书缺少关键字:", ", ".join(result["missing"]))
        return 1
    print("说明书关键字检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
