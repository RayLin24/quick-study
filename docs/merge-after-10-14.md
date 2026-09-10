# 合入顺序：先 #10–#14，再本批 #36–50

本分支从 **main** 只做清单 **#36–50**。PR #10–#14 覆盖 #1–35，**尚未合并**，这里不重做那些 ID。

建议 coordinator 顺序：

1. [PR #10](https://github.com/RayLin24/quick-study/pull/10) #1–6 stale/blob/图跳转/Mermaid/大纲门/估费
2. [PR #11](https://github.com/RayLin24/quick-study/pull/11) #7–8 Ask BM25 + 源片段
3. [PR #12](https://github.com/RayLin24/quick-study/pull/12) #9–10 pytest CI + README/页脚
4. [PR #13](https://github.com/RayLin24/quick-study/pull/13) #11–20 说明书/MCP stdio/pip/增量/Pages/安全
5. [PR #14](https://github.com/RayLin24/quick-study/pull/14) #21–35 单章重写/Ask SSE/SDK/质量门
6. 本 PR #36–50（P2 收尾，清单 1–50 最后一批）

可能的邻近冲突（本分支刻意不包含 #1–35）：

- #12 改 README clone URL 与 `nodes.py` 页脚。本 PR 只在 README **文末**加竞品对照表，并新增 `docs/clone-bloat.md`；不改 clone 行。
- #13 改 `.gitignore`（不再忽略 `pyproject.toml`）。本 PR 只在文件末尾追加 `.playwright-mcp/` / `.agent-teams/archive/`，不碰 `pyproject.toml` 行。
- #10 会改 `github_blob_url`（钉 sha）。本 PR 用独立的 `split_source_ref` / `append_blob_line`，不改 blob HEAD 逻辑。
- #11 改 `utils/ask_tutorial.py`。本 PR 不改 Ask 检索。
- #14 改 `/v1` SDK、Ask SSE。本 PR 的 OTEL/JSONL 相关字段是加字段，合入后应仍兼容。
