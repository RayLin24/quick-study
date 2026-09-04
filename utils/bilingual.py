from __future__ import annotations


def bilingual_enabled(flag) -> bool:
    return bool(flag)


def other_language(language: str) -> str:
    text = (language or "").strip().lower()
    if text == "chinese" or text.startswith("zh"):
        return "english"
    return "Chinese"


def bilingual_note(language: str) -> str:
    alt = other_language(language)
    return f"Also keep a short {alt} subtitle under the main heading."
