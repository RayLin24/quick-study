"""VitePress 组装：chapters/ + 大纲 + 术语表 + Demo 索引 → output/book/ 静态站点骨架。

只写文件不装依赖；`npm i && npm run docs:dev` 即可预览（design.md 4.6.4）。
"""
from __future__ import annotations

import json
import re
import shutil

from quickstudy.storage import Workspace, now_iso


def _vitepress_config(book_title: str, sidebar: list[dict]) -> str:
    items = ",\n      ".join(
        "{ text: %s, link: %s }" % (json.dumps(s["text"], ensure_ascii=False),
                                    json.dumps(s["link"], ensure_ascii=False))
        for s in sidebar)
    return f"""import {{ defineConfig }} from 'vitepress'

export default defineConfig({{
  title: {json.dumps(book_title, ensure_ascii=False)},
  description: '由 quickstudy 自动生成：官方文档重编的中文学习手册',
  lang: 'zh-CN',
  themeConfig: {{
    sidebar: [
      {items}
    ],
    outline: {{ level: [2, 3], label: '本页目录' }},
  }},
}})
"""


def assemble_book(ws: Workspace, outline: dict, chapters: list[dict],
                  chapters_meta: list[dict]) -> dict:
    """组装 output/book/。chapters: [{chapter, filename}]；返回产物清单。"""
    book = ws.path("output", "book")
    if book.exists():
        shutil.rmtree(book)
    (book / "chapters").mkdir(parents=True)
    (book / "appendix").mkdir()
    (book / ".vitepress").mkdir()

    sidebar = [{"text": "开始阅读", "link": "/"}]
    for item in chapters:
        ch, fname = item["chapter"], item["filename"]
        shutil.copyfile(ws.path("chapters", fname), book / "chapters" / fname)
        sidebar.append({"text": f"第{ch['no']}章 {ch['title']}", "link": f"/chapters/{fname[:-3]}"})
    sidebar.append({"text": "附录：术语表", "link": "/appendix/glossary"})
    sidebar.append({"text": "附录：Demo 索引", "link": "/appendix/demos"})

    total_h = sum(c["chapter"].get("est_hours", 0) for c in chapters)
    (book / "index.md").write_text(
        f"# {outline['book_title']}\n\n"
        f"> 由官方文档自动重编 · 共 {len(chapters)} 章 · 预计 {total_h:.0f} 学时\n\n"
        + "\n".join(f"{c['chapter']['no']}. [{c['chapter']['title']}]"
                    f"(chapters/{c['filename'][:-3]}) — {c['chapter'].get('summary', '')}"
                    for c in chapters)
        + f"\n\n---\n生成时间: {now_iso()} · 工具: quickstudy M4\n",
        encoding="utf-8")

    glossary = ws.read_json("glossary.json") or {}
    g_lines = ["# 术语表\n", "| 英文 | 推荐译名 | 说明 |", "| --- | --- | --- |"]
    for t, e in sorted(glossary.get("terms", {}).items(), key=lambda kv: kv[0].lower()):
        zh = "（保留英文）" if e.get("keep_english") else e.get("translation", "")
        g_lines.append(f"| {t} | {zh} | {e.get('note', '')} |")
    (book / "appendix" / "glossary.md").write_text("\n".join(g_lines) + "\n", encoding="utf-8")

    d_lines = ["# Demo 索引\n",
               "以下 Demo 均在**无网络 Docker 沙箱**中实际运行通过（M3），逐行中文注释。\n"]
    seen = set()
    for report in sorted(ws.path("demos").glob("*/*/exec_report.json")):
        r = json.loads(report.read_text(encoding="utf-8"))
        if r.get("status") != "passed" or report.parent.name in seen:
            continue
        seen.add(report.parent.name)
        rel = report.parent.relative_to(ws.dir)
        d_lines.append(f"## {r.get('name', report.parent.name)}\n"
                       f"- 来源: [{r.get('title', '')}]({r.get('url', '')})\n"
                       f"- 代码: `workspace/{rel.as_posix()}/`（`python main.py` 直接运行）\n")
    if not seen:
        d_lines.append("（本手册各章未引用沙箱验证 Demo）")
    (book / "appendix" / "demos.md").write_text("\n".join(d_lines), encoding="utf-8")

    (book / ".vitepress" / "config.mts").write_text(
        _vitepress_config(outline["book_title"], sidebar), encoding="utf-8")
    (book / "package.json").write_text(json.dumps({
        "name": re.sub(r"[^\w-]+", "-", outline["book_title"].lower())[:40] or "quickstudy-book",
        "private": True,
        "scripts": {"docs:dev": "vitepress dev", "docs:build": "vitepress build"},
        "devDependencies": {"vitepress": "^1.6.0"},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"book_dir": str(book), "chapters": len(chapters),
            "files": ["index.md", ".vitepress/config.mts", "package.json",
                      "appendix/glossary.md", "appendix/demos.md"]
                     + [f"chapters/{c['filename']}" for c in chapters],
            "chapters_meta": chapters_meta}
