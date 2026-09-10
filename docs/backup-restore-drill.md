# 备份加密与恢复演练（#47）

默认 `POST /api/ops/backup` 只打 **output 目录** 的 zip。密钥与 LLM 缓存默认 **不进包**。

## 什么进、什么不进

| 路径 | 默认备份？ | 原因 |
| --- | --- | --- |
| `output/`（教程 Markdown、meta、批注） | **是** | 产物 |
| `llm_cache/` / `llm_cache.json` | **否** | 含 prompt 原文，可能泄漏源码与密钥片段 |
| `.env` / `OPENROUTER_API_KEY` / `QUICK_STUDY_TOKEN` | **否** | 密钥永不入库、不进 zip |
| `crawl_cache/` | **否** | 可再拉；体积大 |

需要顺带归档缓存时，单独复制目录，不要与教程备份混用同一口令文件。

## 可选口令加密

`BackupIn.passphrase` 或环境变量 `BACKUP_PASSPHRASE` 非空时，zip 再包一层 `QS1`（PBKDF2-HMAC-SHA256 + HMAC + SHA256-CTR）。这是 **本机演练用轻量加密**，不是 HSM / 合规保险柜。

恢复：`POST /api/ops/restore` 带同一 `passphrase`，或 CLI：

```bash
python -c "from utils.disaster_recovery import restore_output; restore_output('output-backup-xxx.zip.qs1', 'output', passphrase='…')"
```

## 演练步骤（无密钥）

1. 准备一个不含 `.env` 的 `output/Demo/`。
2. `POST /api/ops/backup` `{"passphrase":"drill-only"}` → 得到 `.qs1`。
3. 把 `output/Demo` 移走或改名。
4. `POST /api/ops/restore` 同一口令 → Demo 目录回来。
5. 错误口令应失败，且不得写出半包明文。
6. 确认恢复后的目录里没有 `.env` / API key。
