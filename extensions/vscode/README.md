# Quick Study VS Code / Cursor 扩展（#46 MVP scaffold）

侧栏列出本机 `GET /api/tutorials`，并对选中教程 `POST /api/tutorials/{name}/ask`。

这是 **scaffold**，不发布商店。Cursor 与 VS Code 共用同一 `package.json`。

## 加载

1. 本机先起 `python -m web.serve --host 127.0.0.1 --port 8000`
2. VS Code / Cursor → 扩展 → 从文件夹加载 `extensions/vscode`
3. 设置 `quickStudy.baseUrl`（默认 `http://127.0.0.1:8000`）
4. 只读令牌可填 `quickStudy.token`（不要写进仓库）

Ask 走只读权限（见 `docs/readonly-permission-matrix.md`）。生成教程仍用 Web / CLI。
