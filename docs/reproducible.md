# 可复现自托管：锁文件与 SBOM（#26）

- 运行时依赖：`requirements.txt`（下限）。
- **钉死版本**：`requirements.lock`（`pip install -r requirements.lock`）。
- `uv.lock`：本仓库 `.gitignore` 仍忽略它（#14 的 `pyproject.toml` 在未合入的 PR #13）。需要 uv 时在本地生成，不要提交密钥。
- **SBOM 路径**：`sbom/cyclonedx.json`（CycloneDX 1.5 JSON，由锁文件生成）。再生：

```bash
python scripts/write_sbom.py
```

不要把 API key 写进锁或 SBOM。
