# GitHub Action 一键装

仓库内模板：`.github/workflows/quick-study.yml`。

1. 把该文件复制到目标仓 `.github/workflows/`，或本仓直接 `workflow_dispatch`。
2. 在仓库 Secrets 写入 `OPENROUTER_API_KEY`（不要写进 YAML）。
3. Actions → quick-study → Run workflow，填仓库 URL。
4. 产物在 artifact `tutorial`。

密钥只走 GitHub Secrets，不上 argv。

## 本仓 pytest CI（与上面的一键生成模板无关）

`.github/workflows/pytest.yml` 在 **push** 和 **pull_request** 上跑 `python -m pytest -q`。失败则该 check 红，应设为 required status check（或等价规则）以挡住合并。不要把 pytest 塞进 `quick-study.yml` 的 `workflow_dispatch` 生成模板。
