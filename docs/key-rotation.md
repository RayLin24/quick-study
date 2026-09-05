# 密钥轮换指引（不改业务）

检测接口：`GET /api/ops/keys`（只报告「有哪些环境变量/文件」，不回传密钥值）。

1. 在 OpenRouter / GitHub / 飞书等控制台作废旧密钥。
2. 更新本机 `.env` 或部署环境变量：`OPENROUTER_API_KEY`、`GITHUB_TOKEN`、`QUICK_STUDY_TOKEN`、`QUICK_STUDY_READ_TOKEN`。
3. 不要把新密钥写进 git、Docker 镜像层或命令行。
4. 重启 `uvicorn` / worker。
5. 打开 `/healthz`，确认 `has_llm_key` 为 true。
6. 若轮换了访问令牌，清浏览器 cookie `quick_study_token`。
