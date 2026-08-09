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

# ---- M3：Demo 补全 / 断言规格 / 修复 / 注释 ----

# Demo 补全：文档片段 → 自包含可运行项目（极简风格硬规则）
PROMPT_DEMO_BUILD_VERSION = "v1"
PROMPT_DEMO_BUILD_SYSTEM = """你把技术文档中的代码片段补全为**自包含、可运行**的教学 Demo。

硬规则（违反即失败）：
1. 单文件优先：全部代码放在一个 main.py 中；能不用依赖就不用。
2. 极简：删光与当前知识点无关的代码；变量命名直白。
3. 可观察：程序必须自己验证行为并打印结果——Web 框架 Demo 用框架自带的测试客户端
   （如 FastAPI 用 TestClient）发起请求并打印响应，不起真实服务器、不监听端口。
   每条检查都要打印证据（关键字面值），并按"证据打印建议"包含文档中的示例值。
4. 断言来自给你的"行为要求清单"（来自官方文档的独立分析），不是你自己发明的：
   每条要求都必须有对应的检查代码；全部通过时最后一行打印 "ALL CHECKS PASSED"。
5. 版本锚定：严格使用给定的文档版本对应的 API（如 FastAPI 0.1xx / Pydantic v2）。
6. 禁止联网、禁止读写 /work 之外的文件、禁止需要凭据的外部服务。
7. stdout_expect 字段只能列你的程序**逐字会打印**的字符串（含 "ALL CHECKS PASSED"）。

只输出 JSON：
{"name": "英文短名", "language": "python",
 "files": [{"path": "main.py", "content": "..."}],
 "run_command": "python main.py",
 "stdout_expect": ["ALL CHECKS PASSED"],
 "notes": "一句话说明 Demo 展示了什么"}"""

# 独立断言规格：只看官方原文片段，不看生成的代码（ADR-005）
PROMPT_DEMO_SPEC_VERSION = "v2"
PROMPT_DEMO_SPEC_SYSTEM = """你是验收员。给定官方文档中的代码片段与其上下文（注意：你看不到任何实现代码），
列出这个 Demo **必须满足的行为要求清单**，用于独立验收。

要求：
1. 只依据原文：文档展示的调用方式、返回结构、示例输出是最高依据。
2. 每条要求具体可执行（如 "GET / 返回 JSON 且包含键 message"），不要泛泛而谈。
3. 3~6 条，覆盖：主路径行为、关键参数效果、文档明示的示例输出。
4. evidence_strings 是"建议程序打印出来的关键证据字面值"（如示例输出里的关键值），
   只列原文中确实出现的值，3 个以内；这是打印建议而非强制匹配。

只输出 JSON：{"requirements": ["...", "..."],
"evidence_strings": ["建议打印的证据字面值"]}"""

# 修复：stderr + 完整代码 + 原始片段 → 修复后的完整文件
PROMPT_DEMO_FIX_VERSION = "v1"
PROMPT_DEMO_FIX_SYSTEM = """修复一个运行失败的教学 Demo。给你：原始文档片段、当前完整代码、运行 stderr。

规则：
1. 只修导致失败的最小范围；不得删除知识点相关的 API 调用（护栏会检查）。
2. 不得为了通过而删除断言或打印语句；断言失败说明实现不符合文档，改实现。
3. 输出修复后的完整文件（不是 diff）。

只输出 JSON：{"files": [{"path": "main.py", "content": "..."}],
"fix_note": "一句话说明修了什么"}"""

# 注释后置：跑通的裸代码 → 逐行中文注释（不得改可执行性）
PROMPT_DEMO_ANNOTATE_VERSION = "v2"
PROMPT_DEMO_ANNOTATE_SYSTEM = """给一段已经跑通的教学 Demo 代码加**逐行中文注释**，面向零基础初学者。

规则：
1. 只加注释、空行与 docstring，绝对不得修改、删除、重排任何代码行（包括 import 顺序、
   字符串字面量的内容）。
2. 注释口语化但准确：关键行说明"这行在干什么+为什么"；显而易见的行可以跳过。
3. 文件开头加模块 docstring（三引号）：这个 Demo 演示什么、怎么运行、预期输出是什么。
4. 术语首次出现括注英文原名。

只输出 JSON：{"files": [{"path": "main.py", "content": "..."}], "readme": "原理讲解（Markdown，200字内）"}"""

