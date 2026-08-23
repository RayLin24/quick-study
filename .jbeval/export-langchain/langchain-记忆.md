# 记忆（Memory）

---
slug: memory
---

# 记忆（Memory）

在本章中，我们将探讨如何在链（Chain）和代理（Agent）中引入记忆，使对话具备上下文连续性。我们会区分短期记忆与长期记忆，并讨论不同记忆模块的取舍。

> 注意：本教程的注册来源为 LangChain 官方 GitHub 仓库（https://github.com/langchain-ai/langchain ）。由于我们无法访问该仓库的具体快照，本章不引用任何具体的类名、API 签名或代码示例。所有代码示例均为教学抽象（teaching abstraction），用于说明概念，实际实现请以仓库中的最新代码和文档为准。

## 为什么需要记忆？

一次对话通常不是孤立的。用户可能在上一个消息中提到一个偏好，然后在下一轮中提问。没有记忆，链或代理会忘记之前的内容，导致回答缺乏连贯性。记忆模块让系统能够存储、检索并利用历史信息。

## 短期记忆与长期记忆

- **短期记忆**（Short-term Memory）：通常指会话内的上下文。它保存最近几轮对话，使当前任务能够引用之前的内容。短期记忆一般有长度限制，可能采用滑动窗口。
- **长期记忆**（Long-term Memory）：指跨会话的持久化信息。它可以存储用户偏好、历史事实或过去任务的结论，并在未来会话中检索使用。

在 LangChain 中，这两类记忆都可以被集成到链和代理的流程中。

## 记忆模块的设计取舍

选择或设计记忆模块时，需要考虑以下几个维度：

### 1. 存储内容

- **原始消息**：直接保存所有对话历史。简单，但会占用大量 token。
- **摘要**：将历史内容压缩为摘要。节省 token，但可能丢失细节。
- **关键实体**：只保存用户提到的实体（如人名、地点）。高效，但需要额外的提取逻辑。

### 2. 检索方式

- **全部注入**：将全部记忆内容放入 prompt。实现简单，但长度有限。
- **向量检索**：使用 embedding 对记忆进行语义相似度搜索，只注入相关部分。适合长期记忆，但需要向量存储。

### 3. 更新策略

- **追加**：每次新消息后追加到记忆。
- **改写**：定期重写记忆，例如更新摘要或删除过时信息。

在 LangChain 的生态中，这些取舍往往通过不同的记忆类来体现。由于无法验证具体类名，我们仅从概念层面描述。

## 实战示例（教学抽象）

以下是一个简化的 Python 示例，演示如何为链添加短期记忆。注意，这段代码不是来自 LangChain 仓库，而是为了说明原理而虚构的。

```python
class ShortTermMemory:
    def __init__(self, max_messages=10):
        self.messages = []
        self.max_messages = max_messages

    def add_message(self, msg):
        self.messages.append(msg)
        if len(self.messages) > self.max_messages:
            self.messages.pop(0)

    def get_context(self):
        return "\n".join(self.messages)
```

```python
# 教学抽象：假设的 LangChain 链
def create_chain_with_memory(memory):
    def chain(message, prompt):
        history = memory.get_context()
        full_prompt = f"{prompt}\nHistory:\n{history}\nHuman: {message}"
        response = generate(full_prompt)  # 假设的生成函数
        memory.add_message(f"Human: {message}")
        memory.add_message(f"AI: {response}")
        return response
    return chain
```

对于长期记忆，可以考虑使用向量数据库检索相关历史。同样，以下为示意代码：

```python
# 教学抽象：长期记忆的检索
def retrieve_long_term(query, vector_store, embedding_model):
    query_vector = embedding_model.embed(query)
    results = vector_store.search(query_vector, top_k=5)
    return "\n".join([r.text for r in results])
```

注意：这些类名和函数名均为教学虚构，不是 LangChain 官方 API。

## 集成到链与代理

在 LangChain 中，记忆通常作为链的一个组件，在每次调用前被读取，并在调用后更新。对于代理，记忆可以影响其推理步骤和工具调用。具体实现细节依赖于仓库中的代码，我们无法在此提供确切的指导。

## 总结

- 记忆是构建有状态对话系统的核心。
- 短期记忆负责会话内上下文，长期记忆负责跨会话持久化。
- 记忆模块的取舍涉及存储内容、检索方式和更新策略。
- 由于证据限制，本章未提供 LangChain 官方 API，请参考 GitHub 仓库获取最新信息。
