# Web 前端设计：输入官网地址 → 生成中文学习手册

日期：2026-08-09 ｜ 状态：已批准 ｜ 前置：M1–M4 流水线已完成并验证（FastAPI 全链路产物）

## 1. 需求与边界

用户在浏览器输入官方文档站 URL，后端驱动 M1–M4 流水线，前端展示分阶段进度，手册生成后在线阅读。

已确认的边界决策：

| 决策点 | 结论 |
|---|---|
| 使用场景 | 本地单机自用：无认证、无多租户、单任务串行 |
| 成本闸门 | 保留：大纲生成后任务暂停，页面展示目录与估价，用户点击确认才全量写作 |
| M3 Demo 阶段 | 可选（默认开启）；Docker 不可用自动跳过并明示，不阻断成书 |
| 阅读形态 | 内置阅读页（前端渲染章节 Markdown）；VitePress 组装保留为离线产物 |
| 架构 | 前后端分离：Vue 3 + Element Plus + Vite；FastAPI 托管 build 产物，免 CORS |

YAGNI 裁剪：不做用户体系、不做多任务并发、不做公开部署、前端 v1 不引测试框架。

## 2. 架构

```
quickstudy/web/        # 后端包
  api.py               # FastAPI 路由（薄层，只做校验/转发/序列化）
  jobs.py              # 任务状态机 + workspace/jobs.json 持久化 + 进度推导
  runner.py            # 流水线执行器：asyncio 串行 worker，调用 run_m1~run_book
web/                   # 前端工程（独立 package.json）
  src/views/           # Home / JobDetail / Reader 三页
  src/api.js           # fetch 封装
```

- 启动：`quickstudy serve [--port 8600]` → uvicorn 起 FastAPI；生产模式 `StaticFiles` 托管 `web/dist`，无 CORS。开发模式 vite dev server 将 `/api` 代理到 8600。
- 执行模型：进程内单 asyncio worker + 串行队列；同一时刻最多一个任务在跑（Docker 沙箱与 LLM 成本本就不支持并发）。历史任务浏览、只读 API 不受队列限制。
- 流水线复用：`run_m1 / run_m2 / run_m3 / run_outline / run_book` 零改动接入——它们本就是 async，且全部产物落盘（断点续跑与进度推导的数据源）。

## 3. 任务状态机

```
queued → crawling → organizing → demoing → outlining → awaiting_confirm → writing → done
              旁路终态：failed（任一阶段异常）／标记：skipped_demos、interrupted
```

- **进度从产物推导，不解析日志**：crawl 读 manifest.json 页数与解析成功数；organize 读 graph.json/glossary.json 是否存在及概念数；demo 数 exec_report.json 个数；write 读 chapters/state.json 已成章数/总章数。断点续跑后进度天然正确。
- **持久化**：`workspace/jobs.json` 任务注册表（job_id、url、task_id、状态、选项、时间戳、各阶段摘要快照）。服务重启后历史任务可展示；崩溃时处于运行态的任务标 `interrupted`，提供"续跑"操作（新建同 URL 任务，LLM 缓存使命中阶段零成本）。
- **运行日志**：runner 把所跑阶段的 logging 输出同时写到 `workspace/{task_id}/job.log`；`GET /api/jobs/{id}` 返回其末尾 20 行作为 `recent_log`（详情页日志尾的数据源）。
- **取消**：jobs.json 置取消标志位，runner 在阶段间检查点生效（阶段内不硬杀，避免写半个产物）；`awaiting_confirm` 状态下的"放弃"立即生效。

## 4. API

全部 JSON；错误统一 `{detail: str}` + 合适的状态码。

| 端点 | 说明 |
|---|---|
| `POST /api/jobs` | 新建任务 `{url, with_demos=true, max_pages?}`。前置校验：URL http(s) 可解析、`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` 已配置；失败 400 |
| `GET /api/jobs` | 任务列表（状态、进度、时间、书名若已生成） |
| `GET /api/jobs/{id}` | 详情：状态机阶段、各阶段 summary（取自 report.json）、token 成本（llm_cost_*.json 聚合） |
| `GET /api/jobs/{id}/outline` | 大纲 JSON + 分章估价（闸门确认页） |
| `POST /api/jobs/{id}/confirm` | 仅 `awaiting_confirm` 可调用：→ writing，触发 run_book |
| `POST /api/jobs/{id}/cancel` | 取消（语义见状态机节） |
| `GET /api/jobs/{id}/book` | 大纲树 + 章节元信息（阅读页目录） |
| `GET /api/jobs/{id}/chapters/{filename}` | 章节 Markdown 原文 + glossary 子集（前端渲染） |

## 5. 前端页面

1. **首页**：URL 输入 + Demo 开关（默认开）+ 历史任务卡片（状态标签、进度条、时间）。有任务在跑时新建入口置灰并提示"单任务串行"。
2. **任务详情页**：Element Plus Steps 展示五阶段（闸门为暂停节点）；当前阶段进度数字；累计 token 成本；最近日志尾（轮询 `GET /api/jobs/{id}` 返回的 recent_log 字段，后端取日志文件末尾 20 行）。`awaiting_confirm` 时渲染大纲树 + 每章估价 + 【确认写书】【放弃】。
3. **阅读页**：左章节树右正文，markdown-it + highlight.js 渲染；附录（术语表、Demo 索引）作为特殊节点挂在章节树底部。

## 6. 错误处理

- 阶段异常 → `failed` + 错误摘要 + 建议操作（同 URL 新建任务即续跑）。
- Docker 不可用（`docker_available()` 为假）→ M3 跳过，任务标 `skipped_demos`，详情页明示。
- LLM 调用失败（网关重试耗尽）→ 该阶段 `failed`，错误透传。
- 闸门长期无人确认 → 任务停留 `awaiting_confirm`，零成本，无超时。

## 7. 测试

- 后端：pytest + httpx ASGI 契约测试——新建/列表/详情/闸门确认/取消/进度推导/前置校验 400；pipeline 函数全部 mock，不触网不触 Docker。
- 前端：手动验收三页面（v1 不引测试框架）。
- e2e 冒烟：以现有 `workspace/fastapi-tiangolo-com` 产物起服务，验证只读 API 与阅读页渲染。

## 8. 依赖与变更面

- 后端新增：`fastapi`、`uvicorn`（pyproject `web` extra）。
- 前端新增：`web/` 工程（vue、vue-router、element-plus、markdown-it、highlight.js、vite）。
- CLI 新增 `serve` 子命令；现有流水线代码零改动。
- 环境变量沿用进程环境（当前无 dotenv 加载，与 CLI 行为一致）。
