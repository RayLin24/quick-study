# 链（Chains）

在 LangChain 中，链（Chain）是将多个组件（如提示词模板、语言模型、输出解析器）串联成一个完整推理流程的核心概念。最初 LangChain 通过 `Chain` 基类提供各类链，后来引入 `Runnable` 接口和 LCEL（LangChain Expression Language）来统一表达任意组件的调用。

> 说明：本章基于 LangChain 官方仓库（https://github.com/langchain-ai/langchain）的一般设计原则编写。由于未引用特定代码快照，部分 API 名称和签名可能随版本变化，请以你当前使用的官方文档为准。

## 为什么需要链

单个 LLM 调用通常不能满足真实应用：你可能需要先格式化输入、再调用模型、最后将输出解析为结构化数据。链把这些步骤固定下来，使流程可预测、可复用、可替换。

## 从 Chain 基类到 Runnable

早期版本提供 `Chain` 抽象，例如 `LLMChain`。现在更推荐直接使用 `Runnable` 协议。一个 `Runnable` 是一个可被调用的组件，支持统一的方法：

- `invoke`：单次调用，返回结果
- `batch`：批量调用
- `stream`：流式返回部分结果
- `ainvoke` / `abatch` / `astream`：异步版本

通过实现这些方法，模型、提示词模板、输出解析器都可以被视为 `Runnable`。

## LCEL：用管道符组合链

LCEL 使用 `|` 操作符将两个 `Runnable` 组合成新的 `Runnable`：左边输出成为右边输入。例如：

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser

prompt = ChatPromptTemplate.from_template("用一句话解释{concept}")
model = ChatOpenAI(model="gpt-4")
parser = StrOutputParser()

chain = prompt | model | parser
```

调用 `chain.invoke({"concept": "Runnable"})` 会依次执行提示词格式化、模型推理和字符串解析。

（这里的 `ChatPromptTemplate`、`ChatOpenAI`、`StrOutputParser` 是常见示例，具体实现取决于你安装的包版本。）

## 构建可预测的推理流程

链的优势在于固定流程。你可以把复杂逻辑拆分成多个可测试的组件，再自由组合。

### 基础链示例

```python
from langchain_core.runnables import RunnableLambda

# 自定义函数也被视为 Runnable
def wrap(text: str) -> dict:
    return {"question": f"请回答：{text}"}

def clean(answer: str) -> str:
    return answer.strip()

chain = (
    RunnableLambda(wrap)
    | model
    | parser
    | RunnableLambda(clean)
)
```

### 使用 `RunnableSequence` 或 `RunnableParallel`

除了管道符，你还可以显式使用 `RunnableSequence`。`RunnableParallel` 可以并行运行多个 Runnable，适合需要独立计算的分支：

```python
from langchain_core.runnables import RunnableParallel

parallel = RunnableParallel(
    summary=chain1,
    keywords=chain2,
)
```

### 添加回退与重试

为了让流程更可靠，LangChain 支持为 Runnable 配置回退（fallback）和重试：

```python
model_with_fallback = model.with_fallbacks([backup_model])
```

## 运行与调试

你可以通过 `chain.invoke(...)`、`chain.batch([...])`、`chain.stream(...)` 运行链。调试时可以使用 LangSmith 或简单的日志输出，确保每个中间步骤符合预期。

## 小结

链是 LangChain 的核心抽象。借助 `Runnable` 和 LCEL，你可以用少量代码构建清晰、可组合、可预测的推理流程。理解链的内部机制，是进一步掌握 Agent 与记忆功能的基础。

> 提示：由于本教程未引用具体代码快照，以上示例仅为教学示意。实际使用时请查阅你安装的 LangChain 包文档，确认类名、方法签名和版本兼容性。