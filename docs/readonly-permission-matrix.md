# 只读令牌权限矩阵（#45）

环境变量：

- `QUICK_STUDY_TOKEN` — 写令牌（生成、删除、备份、改教程）
- `QUICK_STUDY_READ_TOKEN` — 只读令牌（列表、阅读、Ask）
- `QUICK_STUDY_DEMO=1` — 演示模式：所有生成类 POST 一律 `AUTH_DEMO` / 403

登录页表单只接受 **写令牌**。只读令牌请用 `Authorization: Bearer` 或 Cookie，不要走 `/login`。

## 矩阵

| 操作 | 路径 | 写令牌 | 只读令牌 | 说明 |
| --- | --- | --- | --- | --- |
| 列表 / 阅读 | `GET /api/tutorials`、`GET /t/…` | 是 | 是 | 读 |
| Ask（Web / MCP） | `POST /api/tutorials/{n}/ask`、MCP `ask` | 是 | **是** | **读**：只根据已生成教程回答，不写仓、不生成 |
| 批注 / 收藏 | `POST …/annotations`、`…/star` | 是 | 是 | 读侧本地笔记，不算生成 |
| 预检 / 生成 | `POST /api/jobs`、`/api/jobs/preview` | 是 | **否** `AUTH_READ_ONLY` | **写** |
| 删除 / 重命名 | `DELETE/POST /api/tutorials/{n}` | 是 | **否** | **写** |
| 备份 / 恢复 | `POST /api/ops/backup`、`/api/ops/restore` | 是 | **否** | **写**（含加密口令） |
| PR 回写旧章 | `POST /api/tutorials/{n}/patch-from-pr` | 是 | **否** | **写** |
| MCP list / read | `list_tutorials` / `read_chapter` | 是 | 是 | 读 |
| 演示模式生成 | 任何 generate 路径 | **否** `AUTH_DEMO` | **否** | 与令牌无关 |

结论：**MCP Ask ≠ 写**。生成、删除、备份、教程回写才是写。
