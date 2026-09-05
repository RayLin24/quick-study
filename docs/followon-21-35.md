# 后续清单 21–35

| # | 能力 | 怎么验 |
|---|------|--------|
| 21 | 教程全文搜索 | `GET /api/search?q=入口` |
| 22 | 标签/分组 | `POST /api/tutorials/{name}/tags` |
| 23 | 导入教程 zip | `POST /api/tutorials/import` |
| 24 | Obsidian/Notion 导出 | `/api/tutorials/{name}/obsidian.zip`、`/notion.md` |
| 25 | EPUB | `/api/tutorials/{name}/export.epub` |
| 26 | RSS/Atom | `/feed.xml` `/rss.xml` |
| 27 | 飞书卡片 + 钉钉 markdown | `DINGTALK_WEBHOOK_URL`，卡片含状态/文件/错误 |
| 28 | GitHub Action 模板 | `.github/workflows/quick-study.yml` |
| 29 | GitLab CI 组件 | `.gitlab-ci/quick-study.yml` |
| 30 | 只读演示 | `QUICK_STUDY_DEMO=1` 后 POST `/api/jobs` → 403 |
| 31 | 审计日志 | `GET /api/audit`（token/session 哈希，无账号） |
| 32 | IP/token 限速 | `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` |
| 33 | 密钥轮换 | `docs/key-rotation.md` + `GET /api/ops/keys` |
| 34 | OTEL traces | `OTEL_TRACES=1` → `output/traces.jsonl` |
| 35 | JSONL 运行日志 | `output/run.jsonl`，可用 `jq` |

```bash
python3 -m pytest -q
```
