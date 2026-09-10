"""Grouped navigation for workbench / compare / digest / PR / heatmap / quality / presets."""

from __future__ import annotations

NAV_GROUPS = (
    {
        "id": "read",
        "label": "阅读与质量",
        "links": [
            {"id": "quality", "label": "质量评分", "href": "/tools#quality", "needs_tutorial": True},
            {"id": "heatmap", "label": "热力图", "href": "/tools#heatmap", "needs_tutorial": True},
            {"id": "glossary", "label": "术语表", "href": "/tools#glossary", "needs_tutorial": True},
            {"id": "week", "label": "一周路径", "href": "/tools#week", "needs_tutorial": True},
        ],
    },
    {
        "id": "work",
        "label": "工作台",
        "links": [
            {"id": "workbench", "label": "工作台", "href": "/tools#workbench", "needs_tutorial": False},
            {"id": "presets", "label": "预设", "href": "/tools#presets", "needs_tutorial": False},
            {"id": "jobs", "label": "任务历史", "href": "/jobs", "needs_tutorial": False},
        ],
    },
    {
        "id": "compare",
        "label": "对比与导读",
        "links": [
            {"id": "compare", "label": "两仓对比", "href": "/tools#compare", "needs_tutorial": False},
            {"id": "pr", "label": "PR 导读", "href": "/tools#pr", "needs_tutorial": False},
            {"id": "digest", "label": "Digest", "href": "/tools#digest", "needs_tutorial": False},
        ],
    },
    {
        "id": "export",
        "label": "导出",
        "links": [
            {"id": "pages", "label": "GitHub Pages", "href": "/tools#pages", "needs_tutorial": True},
            {"id": "offline", "label": "离线 HTML", "href": "/tools#offline", "needs_tutorial": True},
            {"id": "zip", "label": "zip", "href": "/tools#zip", "needs_tutorial": True},
        ],
    },
)


def tools_nav(*, tutorial: str | None = None) -> list[dict]:
    groups = []
    for group in NAV_GROUPS:
        links = []
        for item in group["links"]:
            href = item["href"]
            if tutorial and item.get("needs_tutorial") and href.startswith("/tools#"):
                href = f"{href}?t={tutorial}"
            links.append({**item, "href": href})
        groups.append({"id": group["id"], "label": group["label"], "links": links})
    return groups
