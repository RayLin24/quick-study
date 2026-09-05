# Raycast / Alfred 触发 Ask

脚本不接受密钥参数，只用环境变量 `QUICK_STUDY_TOKEN` / `QUICK_STUDY_URL`。

## Raycast

导入 `scripts/raycast-ask.sh` 为 Script Command。参数 1 = 教程名，参数 2 = 问题。

## Alfred

Workflow Run Script 指向 `scripts/alfred-ask.sh`，参数：`Demo 入口在哪`。
