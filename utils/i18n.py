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
        "skip_main": "跳到主内容",
        "skip_ask": "跳到提问",
        "advanced": "高级选项",
        "auth_needed": "需要访问令牌",
        "auth_readonly": "只读令牌不能生成、删除或备份",
        "auth_demo": "演示模式禁止生成",
        "gallery": "精选教程廊",
        "map_inspect": "map_mode 切片",
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
        "skip_main": "Skip to main content",
        "skip_ask": "Skip to Ask",
        "advanced": "Advanced options",
        "auth_needed": "Access token required",
        "auth_readonly": "Read-only token cannot generate, delete, or backup",
        "auth_demo": "Demo mode blocks generation",
        "gallery": "Showcase gallery",
        "map_inspect": "map_mode slices",
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
