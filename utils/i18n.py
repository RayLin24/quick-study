"""UI language toggle: Chinese / English (feature 19)."""

from __future__ import annotations

STRINGS = {
    "zh": {
        "brand": "Quick Study",
        "tag": "把代码库变成可读教程",
        "generate": "生成教程",
        "library": "已生成",
        "ask": "问这个仓",
        "ask_hint": "主依据是已生成教程。源码片段默认关闭，仅可选附带 *source: 短窗。",
        "ask_source_opt": "附带源文件片段（默认关闭）",
        "ask_layer_tutorial": "教程",
        "ask_layer_source": "源码",
        "copy_md": "复制为 Markdown",
        "jobs": "任务历史",
        "pause": "暂停",
        "resume": "继续",
        "queue": "排队",
        "quality": "质量评分",
        "learn": "你将学到",
        "next": "下一步",
        "search": "搜索教程",
        "demo": "演示模式",
        "lang": "中文",
    },
    "en": {
        "brand": "Quick Study",
        "tag": "Turn a codebase into a readable tutorial",
        "generate": "Generate tutorial",
        "library": "Library",
        "ask": "Ask this repo",
        "ask_hint": "Answers use the generated tutorial. Source snippets are off by default.",
        "ask_source_opt": "Include source snippets (off by default)",
        "ask_layer_tutorial": "Tutorial",
        "ask_layer_source": "Source",
        "copy_md": "Copy as Markdown",
        "jobs": "Job history",
        "pause": "Pause",
        "resume": "Resume",
        "queue": "Queue",
        "quality": "Quality score",
        "learn": "You will learn",
        "next": "Next step",
        "search": "Search tutorials",
        "demo": "Demo mode",
        "lang": "English",
    },
}


def normalize_ui_lang(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in {"en", "english", "eng"}:
        return "en"
    return "zh"


def t(lang: str | None, key: str) -> str:
    table = STRINGS.get(normalize_ui_lang(lang), STRINGS["zh"])
    return table.get(key, STRINGS["zh"].get(key, key))


def catalog(lang: str | None = None) -> dict:
    code = normalize_ui_lang(lang)
    return {"lang": code, "strings": STRINGS[code]}
