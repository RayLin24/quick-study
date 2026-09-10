from __future__ import annotations

import json
import time
from pathlib import Path

from utils.ask_tutorial import AskResult, ask_tutorial_detailed

THREAD_NAME = "ask_thread.json"
MAX_TURNS = 20


def load_thread(folder: Path) -> list[dict]:
    path = Path(folder) / THREAD_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    turns = data.get("turns") if isinstance(data, dict) else data
    return turns if isinstance(turns, list) else []


def save_thread(folder: Path, turns: list[dict]) -> None:
    path = Path(folder) / THREAD_NAME
    path.write_text(
        json.dumps({"turns": turns[-MAX_TURNS:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_history(turns: list[dict], *, max_chars: int = 4000) -> str:
    lines = []
    for turn in turns[-8:]:
        q = str(turn.get("question") or "").strip()
        a = str(turn.get("answer") or "").strip()
        if q:
            lines.append(f"Q: {q}")
        if a:
            lines.append(f"A: {a}")
    text = "\n".join(lines)
    return text[-max_chars:]


def ask_with_thread(
    folder: Path,
    question: str,
    *,
    persist: bool = True,
    include_source: bool | None = None,
) -> AskResult:
    turns = load_thread(folder)
    history = format_history(turns)
    prompt = question
    if history:
        prompt = f"先前对话：\n{history}\n\n新问题：{question}"
    try:
        raw = ask_tutorial_detailed(folder, prompt, include_source=include_source)
    except TypeError:
        raw = ask_tutorial_detailed(folder, prompt)
    result = raw if isinstance(raw, AskResult) else AskResult(answer=str(raw))
    if persist:
        turns.append(
            {
                "question": question,
                "answer": result.answer,
                "used_chapters": result.used_chapters,
                "ts": int(time.time()),
            }
        )
        save_thread(folder, turns)
    result.routed = True
    return result
