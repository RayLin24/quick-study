# 后续清单 1–20

本批在不引入账号体系、不公开裸挂 LLM 的前提下，补齐 Ask / 队列 / 预算 / 阅读辅助。

| # | 能力 | 怎么验 |
|---|------|--------|
| 1 | Ask 引用跳章内锚点 | 提问后「用了哪些章」是链接，指向 `/t/{教程}/{章}.md#锚点` |
| 2 | Ask 复制 Markdown | 回答旁「复制为 Markdown」 |
| 3 | Ask 失败降级短上下文 | 第一次 LLM 失败会 `top_k=1` 再试，`degraded=true` |
| 4 | 单机任务队列 | 勾选「忙碌时排队」后第二次生成返回 `queued`，`output/job_queue.json` 不丢 |
| 5 | 任务历史 | 打开 `/jobs`，看成败 / usage / 耗时 / 估费用 |
| 6 | token 熔断 | `TOKEN_BUDGET_SESSION` / `TOKEN_BUDGET_DAILY`，超限 429 |
| 7 | 供应商估费用 | 预检卡片与历史页显示 USD（按供应商标价） |
| 8 | 质量评分 | `/api/tutorials/{name}/quality`，目录页横幅 |
| 9 | 你将学到 | 章首三句话 |
| 10 | 智能下一步 | 按 source 重叠推荐，不只 1→N |
| 11 | 两仓对比 | `POST /api/compare-repos` |
| 12 | commit range 导读 | `POST /api/guides/commits` |
| 13 | 依赖图按语言着色 | 生成的 mermaid 含 `classDef` |
| 14 | Mermaid 缩放滚动 | 图上 `+/-/1:1`，Ctrl+滚轮 |
| 15 | 在编辑器打开 | source 旁 `vscode://file/...` |
| 16 | 本地仓 watch | `/api/tutorials/{name}/watch` |
| 17 | 生成暂停/继续 | 首页「暂停」「继续」（非取消） |
| 18 | 失败章重试 | `/api/tutorials/{name}/retry-chapter` |
| 19 | 界面中/英 | 顶栏语言切换，cookie `qs_lang` |
| 20 | 无障碍 | 阅读页字号 / 行距 / 高对比 |

```bash
python3 -m pytest -q
```
