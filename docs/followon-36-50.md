# 后续清单 36–50

| # | 能力 | 怎么验 |
|---|------|--------|
| 36 | 过期标记 | `GET /api/tutorials/{name}/stale`，上游 HEAD ≠ meta.upstream_commit |
| 37 | 最值得讲的 5 个入口 | `POST /api/entries` `{"files":[["main.py","x"],["a_test.py","y"]]}` |
| 38 | 测试文件策略可视化 | `POST /api/test-policy` |
| 39 | 稳定 `/v1` | `GET /v1/tutorials`、`POST /v1/tutorials/{n}/ask`、`POST /v1/jobs` |
| 40 | Python SDK | `from sdk.quick_study import QuickStudy` |
| 41 | GitHub 扩展/书签 | `extensions/github/` + `docs/github-extension.md` |
| 42 | Raycast/Alfred | `scripts/raycast-ask.sh` `scripts/alfred-ask.sh` |
| 43 | 改这一处 | `/api/tutorials/{n}/exercises` |
| 44 | 一周读完 | `/api/tutorials/{n}/week-path` |
| 45 | 共享批注 | `annotations.json` 带 author，可文件同步 |
| 46 | OG 封面 | 有 repo_url 时封面用 `opengraph.githubassets.com` |
| 47 | 今日继续读 | 首页卡片 / `GET /api/continue` |
| 48 | 预设 JSON | `/api/presets` 导入导出 |
| 49 | 备份/恢复 | `POST /api/ops/backup` `POST /api/ops/restore` |
| 50 | 内存水位降并发 | `GET /api/ops/memory?file_count=500` |

```bash
python3 -m pytest -q
```
