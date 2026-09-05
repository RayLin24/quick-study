from __future__ import annotations

import re
from pathlib import Path

from utils.ask_tutorial import HEADING_RE


def chapter_quiz(text: str, *, filename: str = "") -> dict:
    heading = HEADING_RE.search(text or "")
    title = heading.group(1).strip() if heading else filename or "本章"
    sentences = [s.strip() for s in re.split(r"[。.!?\n]", text or "") if 8 <= len(s.strip()) <= 80]
    picks = sentences[:3] or [f"{title} 的核心职责是什么？"]
    questions = []
    for idx, sentence in enumerate(picks, start=1):
        questions.append(
            {
                "id": idx,
                "prompt": f"根据「{title}」：{sentence[:60]}。请用一句话复述要点。",
                "hint": filename,
            }
        )
    return {"title": title, "filename": filename, "questions": questions}


def tutorial_quizzes(folder: Path) -> list[dict]:
    root = Path(folder)
    quizzes = []
    for path in sorted(root.glob("*.md")):
        if path.name in {"index.md", "README.md", "glossary.md"}:
            continue
        quizzes.append(chapter_quiz(path.read_text(encoding="utf-8"), filename=path.name))
    return quizzes
