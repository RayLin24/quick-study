# 暂停 / 继续：支持矩阵（#29）

产品语义是 **协作式检查点**，不是可移植的 SIGSTOP。

| 平台 | 暂停文件 `.job_pause` | SIGSTOP / SIGCONT |
| --- | --- | --- |
| Linux / macOS | 支持（默认） | 仅当 `QS_PAUSE_SIGSTOP=1`。可能冻住进行中的 HTTP。 |
| Windows | 支持 | 不支持 |

写章节点在每章开始时 `wait_if_paused`。`GET /api/jobs/pause-semantics` 返回当前矩阵。

不要把「暂停子进程」理解成跨平台保证；自托管请按检查点续跑（`--resume`）。
