"""Prompt 模板库（ADR-006：改 prompt 必须升版本号，缓存键随版本失效）。

所有模板要求模型只输出 JSON，由 gateway.extract_json 容错解析。
"""

# 概念抽取：输入页面摘要清单（编号），输出概念列表 + 页面引用
PROMPT_CONCEPTS_VERSION = "v1"
PROMPT_CONCEPTS_SYSTEM = """你在为一个技术文档站构建知识图谱。输入是该站点的页面摘要清单（带编号）。
任务：抽取 15~40 个**核心概念**——即"理解这项技术必须掌握的知识点"（如"依赖注入"、"生命周期回调"），而不是页面标题的同义词。

要求：
1. 粒度适中：一个概念通常被 1~10 个页面支撑；过细（半页一个）或过粗（一个概念罩住半个站点）都不好。
2. 覆盖完整：每个页面至少归属一个概念；纯导航/营销页（关于、赞助）可不归属。
3. name 用"中文名（English Name）"格式；description 一句话说明这个概念解决什么问题。
4. pages 字段引用输入清单里的页面编号。

只输出 JSON：{"concepts": [{"name": "...", "description": "...", "pages": [0, 3, 7]}]}"""

# 依赖关系：输入概念清单（编号），输出学习依赖边
PROMPT_RELATIONS_VERSION = "v1"
PROMPT_RELATIONS_SYSTEM = """给定一门技术的核心概念清单（带编号和描述），推断概念间的**学习依赖**：
若"理解 B 通常需要先理解 A"，则存在边 A→B。

要求：
1. 只输出你确信存在的依赖，宁缺毋滥；显而易见的环（互相依赖）只保留更强的一侧。
2. 每条边附一句中文理由。
3. from/to 用概念编号。

只输出 JSON：{"edges": [{"from": 0, "to": 3, "reason": "..."}]}"""

# 术语表：输入候选术语，输出 译名/是否保留英文
PROMPT_GLOSSARY_VERSION = "v1"
PROMPT_GLOSSARY_SYSTEM = """你在为一本面向零基础初学者的中文技术手册编制术语表。
输入是候选术语清单（来自官方文档的概念名、标题高频词、代码标识符）。

对每个术语决定：
- translation：推荐中文译名（业界通行译法优先；无通行译法时给直译）
- keep_english：true 表示正文保留英文不译（如 FastAPI、Pydantic、ORM 这类约定俗成不译的）
- note：可选，一句话说明（首次出现括注建议等）

只输出 JSON：{"terms": [{"term": "...", "translation": "...", "keep_english": false, "note": ""}]}"""

# 页面摘要（Map 阶段）：单页 → 一句话摘要（供全局压缩包与章节衔接）
PROMPT_PAGE_SUMMARY_VERSION = "v1"
PROMPT_PAGE_SUMMARY_SYSTEM = """给定技术文档某页的标题与正文节选，用一句中文概括这页讲了什么（≤60字），
并列出该页涉及的 1~3 个关键术语（英文原名）。
只输出 JSON：{"summary": "...", "terms": ["..."]}"""
