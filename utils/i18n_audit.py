"""Missing-key report for hard-coded Chinese strings (#38)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from utils.i18n import STRINGS

CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
ATTR_HINT = re.compile(
    r"""(?:placeholder|title|aria-label|data-i18n)\s*=\s*["']([^"']+["']?)""",
    re.I,
)
DEFAULT_ROOTS = ("web/templates", "web/static/app.js", "web/static/reader.js", "webapp.py")
SKIP_DIRS = {"vendor", "node_modules", "__pycache__"}


def _catalog_values() -> set[str]:
    values = set()
    for table in STRINGS.values():
        values.update(str(v) for v in table.values())
    values.update(STRINGS.get("zh", {}).keys())
    return values


def _iter_files(root: Path, paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for spec in paths:
        target = root / spec
        if target.is_file():
            found.append(target)
            continue
        if target.is_dir():
            for path in target.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                if path.suffix.lower() in {".html", ".js", ".py"}:
                    found.append(path)
    return found


def _string_literals(text: str, suffix: str) -> list[str]:
    hits: list[str] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return CJK_RE.findall(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                hits.append(node.value)
        return hits
    hits.extend(ATTR_HINT.findall(text))
    for match in re.finditer(r">([^<]{2,200})<", text):
        hits.append(match.group(1))
    for match in re.finditer(r"""["'`]([^"'`]{2,200})["'`]""", text):
        hits.append(match.group(1))
    return hits


def audit_hardcoded_zh(root: Path | None = None, paths: list[str] | None = None) -> dict:
    base = Path(root or Path.cwd())
    catalog = _catalog_values()
    missing = []
    covered = 0
    scanned = 0
    for path in _iter_files(base, list(paths or DEFAULT_ROOTS)):
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        for raw in _string_literals(text, path.suffix.lower()):
            blob = re.sub(r"\s+", " ", raw).strip()
            if not CJK_RE.search(blob):
                continue
            if "{{" in blob or "{%" in blob:
                continue
            if blob in catalog or any(blob == val or blob in val for val in catalog):
                covered += 1
                continue
            missing.append({"file": str(path.relative_to(base)).replace("\\", "/"), "text": blob[:160]})
    # de-dupe
    seen = set()
    uniq = []
    for item in missing:
        key = (item["file"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return {
        "scanned_files": scanned,
        "covered": covered,
        "missing_count": len(uniq),
        "missing": uniq,
    }


def missing_key_report(root: Path | None = None) -> str:
    report = audit_hardcoded_zh(root)
    lines = [
        f"# i18n missing-key report",
        f"scanned={report['scanned_files']} covered={report['covered']} missing={report['missing_count']}",
        "",
    ]
    for item in report["missing"][:80]:
        lines.append(f"- `{item['file']}`: {item['text']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(missing_key_report())
