# 清单 #21–35 状态

合入顺序见 [merge-after-10-13.md](./merge-after-10-13.md)。不重做 #1–20。

| ID | 项 | 状态 |
| --- | --- | --- |
| 21 | 单章重写 API + UI | **done** `POST /api/tutorials/{name}/rewrite-chapter` |
| 22 | Ask SSE + 计费说明 | **done** `/ask/events`，[ask-sse.md](./ask-sse.md) |
| 23 | Citation 锚点回归（mock LLM） | **done** `tests/test_citation_anchors.py` |
| 24 | Monorepo 包选择器 | **done** `POST /api/packages` + 首页扫描 |
| 25 | Bitbucket HTTP tree | **done** classify + tree API |
| 26 | lock / SBOM | **done** `requirements.lock` + `sbom/cyclonedx.json` |
| 27 | `/v1` OpenAPI 稳定面 | **done** [v1-openapi.md](./v1-openapi.md) |
| 28 | SDK preview/export/wait/zip | **done** `sdk/quick_study.py` |
| 29 | pause/resume 矩阵 | **done** 协作检查点默认，[pause-resume.md](./pause-resume.md) |
| 30 | 双模型推荐 + 费用对照 | **done** 默认仍为 glm-5.3-flash |
| 31 | docs drift PR 适配器 MVP | **done** 检测/分支名/受影响章；**stub** 真开 GitHub PR |
| 32 | 图节点 data-id 匹配 | **done** `diagram_click.js` |
| 33 | 质量：覆盖 + 关系密度 | **done** |
| 34 | 搜索 title>source>body | **done** |
| 35 | 自适应学习路径 | **done** `week_path(adaptive=True)` |

**stub：** #31 不在本仓库代开远端 PR（需宿主 git 写权限）。
