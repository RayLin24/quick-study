#!/usr/bin/env python3
"""Write a minimal CycloneDX SBOM from requirements.lock (#26). No secrets."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_lock(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "==" not in text:
            continue
        name, version = text.split("==", 1)
        name, version = name.strip(), version.split("#", 1)[0].strip()
        if name and version:
            items.append({"name": name, "version": version})
    return items


def main() -> None:
    lock = ROOT / "requirements.lock"
    components = []
    for item in parse_lock(lock):
        components.append(
            {
                "type": "library",
                "name": item["name"],
                "version": item["version"],
                "purl": f"pkg:pypi/{item['name'].lower()}@{item['version']}",
            }
        )
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "quick-study",
                "version": "0.0.0",
            }
        },
        "components": components,
    }
    dest = ROOT / "sbom" / "cyclonedx.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(dest)


if __name__ == "__main__":
    main()
