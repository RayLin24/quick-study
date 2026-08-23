# 提示词工程

> **注意**：本教程基于 LangChain 框架的通用实践编写。由于本章未提供来自官方仓库的截图或可验证的证据，所有 API 名称、代码示例和用法描述均属于**教学抽象**，未经实际代码验证。读者在实际使用时请查阅 LangChain 官方文档（https://github.com/langchain-ai/langchain）以确认最新接口。

## 引言

提示词工程（Prompt Engineering）是构建大语言模型应用的关键环节。好的提示词能显著提升模型输出的质量和稳定性。LangChain 提供了三个核心组件来帮助开发者构建可复用、可组合的提示词：

- **提示词模板（PromptTemplate）**：将静态文本与动态变量结合，生成可复用的提示词。
- **示例选择器（ExampleSelector）**：从大量训练示例中动态挑选最相关的少量样本，用于少样本学习（few-shot learning）。
- **输出解析器（OutputParser）**：将模型的原始输出解析为结构化格式（如 JSON、Pydantic 模型），方便下游逻辑使用。

这三个组件可以独立使用，也可以串联成复杂的提示词流水线。下面我们逐一介绍。

## 提示词模板

提示词模板允许你定义一个模板字符串，其中用占位符表示变量。使用时填充变量值，即可生成最终提示词。

### 基础模板（PromptTemplate）

在 LangChain 中，`PromptTemplate` 用于单轮字符串提示词。其核心思想是模板字符串 + 变量映射。

**示意代码（未验证）**：

```python
from langchain.prompts import PromptTemplate

template = "请为以下产品写一句广告语：{product}"

prompt_template = PromptTemplate.from_template(template)

prompt = prompt_template.format(product="智能手表")
print(prompt)
# 输出：请为以下产品写一句广告语：智能手表
```

**要点**：

- 模板变量用花括号 `{}` 包裹。
- 使用 `from_template` 或直接构造 `PromptTemplate` 对象。
- 调用 `format` 方法填充变量。

### 聊天模板（ChatPromptTemplate）

对于聊天模型（如 ChatGPT），提示词包含多条消息（system、human、assistant）。`ChatPromptTemplate` 将不同角色消息组合在一起。

**示意代码（未验证）**：

```python
from langchain.prompts import ChatPromptTemplate

chat_template = ChatPromptTemplate.from_messages([
    ("system", "你是一位专业的营销文案撰写人。"),
    ("human", "产品名称：{product}\n请写一句广告语。"),
])

messages = chat_template.format_messages(product="智能手表")
```

**优点**：

- 支持多角色消息，适配聊天模型。
- 可混合字符串和消息对象，灵活构建对话上下文。

### 组合与复用

模板可以嵌套或拼接。你可以将公共模板抽取出来，形成模板库。例如，定义一个基础模板，再在每个具体任务中扩展。

**教学抽象**：模板就像函数——参数化、可重用、可组合。

## 示例选择器

少样本学习通常需要在提示词中注入几个例子。示例选择器（`ExampleSelector`）负责根据用户输入自动挑选最相关的例子，避免手工固定例子带来的泛化问题。

### 常见策略

- **长度选择器（LengthBasedExampleSelector）**：按文本长度筛选，保证提示词总长度不超过模型限制。
- **相似度选择器（SemanticSimilarityExampleSelector）**：将示例与输入做语义相似度比对，选出最接近的 N 个。

**示意代码（未验证）**：

```python
from langchain.prompts.example_selector import SemanticSimilarityExampleSelector
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma

examples = [
    {"query": "天气怎么样？", "answer": "今天晴转多云。"},
    {"query": "推荐一部电影", "answer": "《流浪地球》不错。"},
    {"query": "如何学习编程？", "answer": "从基础语法开始，多做练习。"},
]

selector = SemanticSimilarityExampleSelector.from_examples(
    examples,
    OpenAIEmbeddings(),
    Chroma,
    k=2,
)

selected = selector.select_examples({"query": "明天会下雨吗"})
print(selected)
# 大概率输出与天气相关的两个例子
```

### 在提示词中使用

将 `ExampleSelector` 与 `PromptTemplate` 结合，可以在填充模板时动态注入示例。

**示意代码（未验证）**：

```python
from langchain.prompts import FewShotPromptTemplate

few_shot_prompt = FewShotPromptTemplate(
    example_selector=selector,
    example_prompt=example_prompt_template,
    prefix="请根据以下示例回答问题：",
    suffix="问题：{input}\n答案：",
    input_variables=["input"],
)
```

**作用**：

- 自动选择最有参考价值的例子，提升模型表现。
- 减少 token 消耗，因为只发送相关的例子。

## 输出解析器

模型输出的通常是原始文本，我们需要将其转换为结构化数据（如字典、Pydantic 对象）才能被程序可靠消费。输出解析器（`OutputParser`）负责这一转换。

### 常用解析器

- `PydanticOutputParser`：基于 Pydantic 模型，自动将文本解析为对象，并校验字段类型。
- `CommaSeparatedListOutputParser`：解析逗号分隔的列表。
- `JSONOutputParser`：直接解析 JSON。

**示意代码（未验证）**：

```python
from langchain.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

class Recipe(BaseModel):
    name: str = Field(description="食谱名称")
    ingredients: list[str] = Field(description="原料列表")

parser = PydanticOutputParser(pydantic_object=Recipe)

# 将解析器融入提示词，要求模型输出符合格式
prompt_template = PromptTemplate(
    template="请为“{dish}”设计一个食谱。\n{format_instructions}\n",
    input_variables=["dish"],
    partial_variables={"format_instructions": parser.get_format_instructions()},
)

# 模型输出文本后，用 parser.parse(text) 得到 Recipe 对象
```

**要点**：

- 解析器需要与提示词中的格式说明配合，确保模型输出可解析的格式。
- 解析失败时可捕获异常，进行修复或重试。

## 组合与复用

将这三种组件串联，可以构建一个清晰的“模板 → 示例 → 解析”流水线。

**综合示意代码（未验证）**：

```python
# 1. 定义示例选择器（省略示例数据）
selector = ...

# 2. 定义提示词模板（使用 few-shot + 格式说明）
prompt_template = FewShotPromptTemplate(
    example_selector=selector,
    example_prompt=example_template,
    prefix="请用 JSON 格式回答。\n{format_instructions}\n",
    suffix="问题：{input}\n答案：",
    input_variables=["input", "format_instructions"],
)

# 3. 定义输出解析器
parser = PydanticOutputParser(pydantic_object=MyModel)

# 4. 在链（Chain）中组合（示意）
# chain = prompt_template | model | parser
```

在 LangChain 中，组件通过 `|` 管道操作符或 `LLMChain` 类进行组合。这样可以将提示词、模型和解析器封装成可复用的单元，为更复杂的链和代理做好准备。

**最佳实践**：

- 将模板、选择器和解析器都作为独立变量，方便测试和替换。
- 为解析器编写清晰的格式说明，提高模型输出符合率。
- 使用示例选择器时，注意向量数据库的持久化，避免重复计算。

## 总结

通过提示词模板、示例选择器和输出解析器，我们可以：

- 将静态提示词与动态数据解耦，实现复用。
- 根据具体输入自适应选择示例，提升少样本效果。
- 将模型输出强制转换为结构化格式，方便程序处理。

这些组件是构建稳定、可维护的 LangChain 应用的基础。下一章将介绍如何将这些组件连接成链（Chain），实现更复杂的工作流。

> 再次提醒：鉴于本章没有提供可验证的证据快照，以上代码和 API 名称仅作教学演示，请以 LangChain 官方最新文档为准。