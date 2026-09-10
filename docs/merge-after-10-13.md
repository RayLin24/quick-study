# 合入顺序：先 #10–#13，再本批 #21–35

本分支从 **main** 只做清单 **#21–35**。PR #10–#13 覆盖 #1–20，**尚未合并**，这里不重做那些 ID。

建议 coordinator 顺序：

1. [PR #10](https://github.com/RayLin24/quick-study/pull/10) #1–6 stale/blob/图跳转/Mermaid/大纲门/估费
2. [PR #11](https://github.com/RayLin24/quick-study/pull/11) #7–8 Ask BM25 + 源片段
3. [PR #12](https://github.com/RayLin24/quick-study/pull/12) #9–10 pytest CI + README/页脚
4. [PR #13](https://github.com/RayLin24/quick-study/pull/13) #11–20 说明书/MCP stdio/pip/增量/Pages/安全
5. 本 PR #21–35

可能的邻近冲突（本分支刻意不包含 #1–20）：

- #10 / #13 都动 `nodes.py`、估费、页脚；本 PR 不改流水线节点。
- #11 改 `utils/ask_tutorial.py`；本 PR 在其上增加 Ask SSE（新文件 `utils/ask_stream.py`），合入后应先合 #11。
- #13 改 `项目说明书.md` / `pyproject.toml` / `.gitignore`；本 PR 的 `docs/reproducible.md` 说明 lock 与 SBOM，不抢 pyproject。
