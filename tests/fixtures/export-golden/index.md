# Golden Export Demo

关系图：

```mermaid
flowchart LR
  fetch[Fetch] --> identify[Identify]
  identify --> write[Write]
```

*source: src/flow.py:L1*

推荐阅读：先 Fetch，再 Identify。
