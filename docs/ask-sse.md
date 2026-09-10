# Ask SSE 计费（#22）

Ask 流式接口：`POST /v1/tutorials/{name}/ask/events`（`/api/.../ask/events` 同实现）。

- **与流水线 SSE 分开**：`GET /v1/jobs/current/events` 只推生成日志，不跑 Ask。
- **一次提问一次账单**：Ask SSE 打开自己的 completions stream，不读 `LLM_STREAM`。
- **默认不二次计费**：空流不会自动再打一枪非流式。需要时才设 `LLM_STREAM_FALLBACK=1`（那是流水线开关，Ask SSE 不用）。
- 浏览器勾选「流式回答」走 Ask SSE；不勾选走原来的 JSON Ask。
