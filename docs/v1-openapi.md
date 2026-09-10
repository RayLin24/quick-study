# `/v1` 稳定面 vs 实验 API

稳定 HTTP 面只保证 **list / ask / jobs**（外加本批产品化的 preview、export、SSE）。机器可读副本：`GET /v1/openapi.json`。

## 稳定

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/tutorials` | 已生成教程列表 |
| POST | `/v1/tutorials/{name}/ask` | 问这个仓（一次性 JSON） |
| POST | `/v1/tutorials/{name}/ask/events` | Ask SSE（**与流水线 job SSE 分开计费**） |
| POST | `/v1/jobs` | 开始生成 |
| GET | `/v1/jobs/current` | 轮询当前任务 |
| GET | `/v1/jobs/current/events` | 流水线日志 SSE |
| POST | `/v1/jobs/preview` | 只爬不写 |
| GET | `/v1/tutorials/{name}/export.zip` | 下载 zip |
| GET | `/v1/openapi.json` | 本表 |

默认模型仍是 OpenRouter `z-ai/glm-5.3-flash`。

## 实验（`/api/*`）

搜索、标签、导入、Obsidian/EPUB、审计、preset、hooks、MCP、质量门、包扫描、单章重写等走 `/api`。客户端 SDK 不要依赖这些路径的稳定性。

## Ask SSE 计费

见 [ask-sse.md](./ask-sse.md)。一次流式提问 = 一次 `chat.completions`。不要在同一问再打非流式，除非显式 fallback。流水线 `LLM_STREAM` 是另一条计费开关，默认关。
