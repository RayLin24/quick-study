# quickstudy 开发说明

技术方案见 `docs/design.md`，架构决策见 `docs/decisions.md`（ADR-001~007），
Web 前端设计/计划见 `docs/superpowers/specs|plans/2026-08-09-*`。

## 当前进度：M4 + Web 前端完成

- **M1**：站点发现（Sitemap+BFS 双引擎、不对称差异告警）→ 礼貌爬取（限速/robots/增量 ETag/JS壳升级渲染）
  → 结构化解析（标题树/代码块/表格/链接/图片 + simhash 去重 + 版本 tag）→ manifest + 覆盖率报告。
- **M2**：结构化切分（标题树骨架，2166 chunks @FastAPI）→ 向量索引（Qdrant 本地模式，百炼 embedding）
  → 知识图谱（40 概念 / 127 概念边：引用+同属确定性边 + K3 依赖边）→ 全局术语表（80 条，人工覆盖层支持）。
  LLM 网关：K3（Anthropic 兼容）+ 输入hash缓存 + 成本台账 + 截断检测重试。
- **M3**：Demo 重构与沙箱校验（Docker `--network none`）：独立断言规格 → 生成 → 自愈≤3轮（符号护栏）→ 注释后置复跑。
- **M4**：学习路径排序（SCC+优先级拓扑）→ 大纲（成本闸门）→ 分章写作（英进中出+摘要链）
  → 质检关卡（结构/占位符/术语/溯源）+ L3 覆盖率 → VitePress 组装。
- **Web**：`quickstudy serve`（FastAPI + Vue3/Element Plus）：输入 URL → 串行流水线 → 大纲闸门确认 → 在线阅读。

## 环境（conda）

```bash
conda env create -f environment.yml   # Python 3.12 + pip 安装 -e ".[dev,m2,render,web]"
conda activate quickstudy
playwright install chromium           # 仅 JS 壳站点渲染升级需要
```

## 使用

```bash
# Web 模式（推荐）：浏览器操作全流程
quickstudy serve                       # http://127.0.0.1:8600

# CLI 模式
quickstudy crawl https://fastapi.tiangolo.com      # M1；--max-pages 30 限量调试
quickstudy organize <url>                          # M2；--no-llm 离线 / --fake-embed 无百炼 key
quickstudy demos <url> --limit 5                   # M3；需 Docker Desktop 运行中
quickstudy outline <url>                           # M4 闸门：目录+估价
quickstudy book <url>                              # M4 成书；--max-chapters 3 试写 / --recheck-qc 离线复核

python -m pytest tests/ -q           # 测试（fixture 站点，无外部网络）
python scripts_smoke.py              # e2e 冒烟（需先 quickstudy serve 另开终端）
```

产物落在 `workspace/{task_id}/`：`manifest.json`（URL清单+指纹+版本+license）、
`raw/*.html`（快照）、`parsed/*.md|.json`、`chunks/*.jsonl`、`graph.json`、`glossary.json`、
`demos/`（exec_report+注释代码）、`outline.json`、`chapters/`（成稿+state.json）、
`output/book/`（VitePress 站点）、`report.json`（L1/L3 覆盖率+告警）、`llm_cost*.json`（token 台账）。

## 路线图（M5 加固扩展）

- Java/中间件沙箱矩阵、增量再生成、成本优化、前端测试框架。
