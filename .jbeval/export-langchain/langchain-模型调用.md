# 模型调用

<!-- slug: 模型调用 -->

> **教学抽象声明**：本教程基于 LangChain 官方仓库（[github.com/langchain-ai/langchain](https://github.com/langchain-ai/langchain)）的通用设计理念编写，但当前没有可引用的快照证据。因此，所有 API 名称、方法签名和行为都是根据常见使用方式进行的教学抽象，可能与实际版本略有出入。请以你安装的 LangChain 版本文档为准。

## 学习问题

1. LLM 与聊天模型有什么本质区别？
2. 如何用同一套代码调用 OpenAI、Anthropic 等不同提供商的模型？
3. 如何从模型获得流式输出，而不是等待全部文本生成完毕？

## 引言

在实际项目中，我们通常不会只依赖某一家模型提供商。LangChain 的价值在于：它把不同提供商的模型封装成统一的接口，让我们可以在不改变业务逻辑的前提下，切换 OpenAI、Anthropic 等后端。本章将聚焦“模型调用”这一核心环节，从最基础的 LLM 与聊天模型差异讲起，再介绍通过统一接口调用不同提供商的方法，最后演示流式输出等高级用法。

## LLM 与聊天模型

在 LangChain 中，模型分为两类：

- **LLM（大语言模型）**：接受一个文本字符串（prompt），返回一个文本补全字符串。这是一种“文本进、文本出”的传统接口。
- **聊天模型（Chat Model）**：接受一个消息列表（例如 `SystemMessage`、`HumanMessage`、`AIMessage`），返回一个 `AIMessage`。聊天模型专门面向对话场景，能够支持系统提示、多轮历史等复杂结构。

从 LangChain 的发展来看，聊天模型已经是主流。即便底层是同一个模型，聊天模型接口也往往能提供更丰富的上下文信息。大多数模型提供商（OpenAI、Anthropic 等）都提供了原生聊天接口，因此 LangChain 的 `ChatOpenAI`、`ChatAnthropic` 也就成了最常用的封装。

为了让两种模型通过同一种方式工作，LangChain 为它们都实现了 `Runnable` 协议。这样，无论是 LLM 还是聊天模型，你都可以使用 `invoke` 来执行调用，使用 `stream` 来流式获取结果。只不过 LLM 直接传字符串，聊天模型需要传消息列表。

下面是一个聊天模型的基础示例：

```python
from langchain_core.messages import HumanMessage, SystemMessage

messages = [
    SystemMessage(content="你是一名乐于助人的助手。"),
    HumanMessage(content="给我讲一个笑话。"),
]
```

注意，这里的 `SystemMessage` 和 `HumanMessage` 都是消息对象。聊天模型接收的就是这样的列表。

## 统一调用接口

LangChain 为不同提供商提供了实现相同接口的包装类。例如 `ChatOpenAI` 负责 OpenAI 系模型，`ChatAnthropic` 负责 Anthropic 系模型。它们都继承自 `BaseChatModel`，因此对外暴露的方法几乎一致。

### 使用 OpenAI 模型

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
response = model.invoke(messages)
print(response.content)  # 输出的是一段文本
```

### 使用 Anthropic 模型

```python
from langchain_anthropic import ChatAnthropic

model = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0.7)
response = model.invoke(messages)
print(response.content)
```

你会发现，两种模型的调用方式完全一样，唯一区别只是模型类和 API 密钥等初始化参数。这就是统一接口带来的好处。

### 用 `init_chat_model` 动态选择

如果你希望将模型名与提供商作为配置项，LangChain 还提供了 `init_chat_model` 函数。这个函数可以根据“模型名 + 提供商”字符串创建出对应的模型实例：

```python
from langchain.chat_models import init_chat_model

model = init_chat_model("gpt-4o-mini", model_provider="openai")
# model = init_chat_model("claude-3-5-sonnet-20241022", model_provider="anthropic")
```

这在需要用同一套代码对接不同模型时非常方便。例如，你可以把 `model` 和 `model_provider` 放在环境变量或配置文件中，从而在开发和测试环境之间快速切换。

注意：`init_chat_model` 的具体位置和签名可能会随版本变化。若你使用的版本中没有该函数，请直接使用 `ChatOpenAI` 或 `ChatAnthropic` 这样的显式包装类。

## 流式输出

对于聊天模型，除了 `invoke` 这种一次性返回完整结果的方式，LangChain 还支持 `stream` 方法，让我们逐块获取模型生成的内容。这在用户体验上非常重要，尤其是需要实时展示打字效果时。

流式输出的基本用法如下：

```python
for chunk in model.stream(messages):
    print(chunk.content, end="", flush=True)
```

这里的 `chunk` 是一个 `AIMessageChunk` 对象，`chunk.content` 是本次生成的新文本片段。把每次的片段拼接起来，就能得到完整的输出。

如果是在异步环境中，可以使用 `astream` 方法：

```python
async for chunk in model.astream(messages):
    print(chunk.content, end="", flush=True)
```

`stream` 和 `astream` 都是 `Runnable` 协议的一部分，因此不仅聊天模型，任何实现该协议的组件（如链）都可以用同样的方式流式输出。

## 流事件（高级）

除了 `stream`，LangChain 还提供了 `astream_events` 方法，用于在复杂链中获取更细粒度的事件。例如，你可以监听模型的开始事件、结束事件、新令牌事件等。它对于构建日志、实现可观测性或精细控制十分有用。

```python
async for event in model.astream_events(input=messages, version="v1"):
    kind = event["event"]
    if kind == "on_chat_model_stream":
        data = event["data"]["chunk"]
        print(data.content, end="", flush=True)
```

这里需要指定 `version="v1"` 来使用当前推荐的事件格式。`astream_events` 的详细字段较多，本教程不再展开。感兴趣的读者可以查阅 LangChain 官方文档，或直接阅读源码。

> 教学抽象说明：以上关于 `astream_events` 的用法是教学抽象，具体事件类型和字段可能在不同版本间有所不同。

## 总结

本章我们介绍了 LangChain 中模型调用的核心内容：

- LLM 与聊天模型的区别，以及消息列表的用法。
- 通过 `ChatOpenAI`、`ChatAnthropic` 等统一接口调用不同提供商的模型。
- 使用 `init_chat_model` 动态创建模型，方便配置化切换。
- 利用 `stream` / `astream` 实现流式输出，提升用户体验。
- 简单了解 `astream_events` 高级事件机制。

在实际开发中，你是否遇到过模型切换困难的问题？或者你觉得流式输出还有哪些更复杂的场景？欢迎在实践中继续探索。

---

*本教程基于 LangChain 官方仓库（https://github.com/langchain-ai/langchain）的通用理念编写，所有示例均为教学抽象，不保证与任一具体版本完全一致。*
