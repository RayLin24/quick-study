# quickstudy 开发说明

技术方案见 `docs/design.md`，架构决策（对已批准优化点的落地约定）见 `docs/decisions.md`。

## 当前进度：M2 完成（模块三+术语表；翻译管线按 ADR-001 并入 M4 写作阶段）

- **M1**：站点发现（Sitemap+BFS 双引擎、不对称差异告警）→ 礼貌爬取（限速/robots/增量 ETag/JS壳升级渲染）
  → 结构化解析（标题树/代码块/表格/链接/图片 + simhash 去重 + 版本 tag）→ manifest + 覆盖率报告。
- **M2**：结构化切分（标题树骨架，2166 chunks @FastAPI）→ 向量索引（Qdrant 本地模式，百炼 embedding）
  → 知识图谱（40 概念 / 127 概念边：引用+同属确定性边 + K3 依赖边）→ 全局术语表（80 条，人工覆盖层支持）。
  LLM 网关：K3（Anthropic 兼容）+ 输入hash缓存 + 成本台账 + 截断检测重试。

## 环境

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Windows
# 可选渲染升级（JS 壳站点）：.venv/Scripts/pip install -e ".[render]" && playwright install chromium
```

## 使用

```bash
# 全量爬取 FastAPI 文档站（礼貌限速默认 5 req/s）
.venv/Scripts/python -m quickstudy.cli crawl https://fastapi.tiangolo.com

# 常用参数
#   --max-pages 30        限量调试
#   --incremental         增量重跑（ETag/内容hash 命中则跳过）
#   --include-prefix /tutorial   范围收窄（ADR-003，可多值）
#   --no-render           禁用 Playwright 渲染升级
#   -v                    调试日志

.venv/Scripts/python -m pytest tests/ -q   # 测试（fixture 站点，无外部网络）

# M2：知识组织（在已有 workspace 上运行；LLM 走 K3，embedding 需 DASHSCOPE_API_KEY）
.venv/Scripts/python -m quickstudy.cli organize https://fastapi.tiangolo.com
#   --no-llm      只建确定性页面引用图（离线）
#   --fake-embed  无百炼 key 时用假向量验证流程（无检索语义）

# M3：Demo 重构与沙箱校验（需 Docker Desktop 运行中）
.venv/Scripts/python -m quickstudy.cli demos https://fastapi.tiangolo.com --limit 5
# 沙箱约定：镜像构建期联网装依赖，Demo 运行时 --network none；
# 通过标准=退出码0+输出非空+预期模式命中；自愈≤3轮带符号护栏；注释后置并复跑确认。
```

产物落在 `workspace/{task_id}/`：`manifest.json`（URL清单+指纹+版本+license）、
`raw/*.html`（快照）、`parsed/*.md|.json`（结构化文档）、`report.json`（L1 覆盖率+漏页清单+差异告警+范围界定建议）。

## 路线图

- **M2 知识组织与写作准备**：切分/向量索引（Qdrant + 阿里百炼 embedding）/概念抽取与知识图谱（NetworkX）/术语表。
  需要 `DASHSCOPE_API_KEY`（见 `.env.example`）与 K3 的 Anthropic 兼容端点配置。
- **M3 Demo 重构**：沙箱矩阵（需启动 Docker Desktop）、ADR-005 校验加固。
- **M4 成书**：大纲即契约（ADR-001 翻译降级为能力、ADR-002 侧边栏主干排序）、L3 内容覆盖率门禁。
- **M5 加固扩展**：Java/中间件沙箱、增量再生成、成本优化。
