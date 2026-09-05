from __future__ import annotations

from pathlib import Path

REQUIRED_KEYWORDS = (
    "QUICK_STUDY_TOKEN",
    "GITHUB_TOKEN",
    "dry-run",
    "OPENROUTER",
    "127.0.0.1",
    "LLM_TIMEOUT",
)


def missing_keywords(text: str, required=REQUIRED_KEYWORDS) -> list[str]:
    return [word for word in required if word not in (text or "")]


def check_manual(path: Path | None = None) -> dict:
    target = Path(path or Path(__file__).resolve().parents[1] / "项目说明书.md")
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    missing = missing_keywords(text)
    return {"path": str(target), "ok": not missing, "missing": missing}
