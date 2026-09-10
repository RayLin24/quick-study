# 减小 clone 体积（#36）

本仓库从上游带了一大批 **已生成的示例教程**（`docs/PocketFlow`、`docs/FastAPI`、`docs/Codex` 等）以及本地代理噪声目录。跑 Quick Study **不需要**这些示例。

## 已忽略的本地噪声

下列目录不应再提交（见根 `.gitignore`）：

| 路径 | 原因 |
| --- | --- |
| `.playwright-mcp/` | 浏览器 MCP 缓存 / 扩展草稿 |
| `.agent-teams/` | agent-team inbox / archive / team.json 噪声 |

这些目录已从 git 树卸载；新 clone 不会再长出它们。

## 体积大的上游示例（不要当运行时依赖）

`docs/` 下除设计文档外，还有上游 Pages 示例教程（AutoGen、Celery、LangGraph…）。它们：

- **不是** 生成本仓教程所必需
- 只给阅读 / 回归夹具（例如 `docs/PocketFlow`）
- **不要** 做成 git submodule（上游会改 URL 与授权）；需要时用 sparse checkout

建议 sparse checkout（只要代码与夹具）：

```bash
git clone --filter=blob:none --sparse https://github.com/RayLin24/quick-study.git
cd quick-study
git sparse-checkout set \
  web utils tests scripts sdk extensions gallery \
  docs/design.md docs/PocketFlow \
  main.py webapp.py nodes.py flow.py mcp_server.py \
  requirements.txt .env.sample README.md 项目说明书.md
```

完整历史仍可用 `git sparse-checkout disable`。不要把 `docs/*.pdf` 或生成物 `output/` 提交进库。
