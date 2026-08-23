## 为什么需要结构化输出

大语言模型的输出通常是自由文本，直接用于程序时容易出错。将输出解析为 JSON 或 Pydantic 对象，可以让下游逻辑获得类型安全的数据。LangChain 通过输出解析器（Output Parser）来实现这一目标。

（来源：https://github.com/langchain-ai/langchain）

> 注意：本教程引用的仓库地址不包含具体快照内容，因此以下 API 签名和示例基于 LangChain 的公开用法与常见文档，无法从该源逐一验证。版本更新后 API 可能变化，请以官方文档为准。

## 输出解析器接口

LangChain 的输出解析器通常继承自 `BaseOutputParser`，核心方法包括：

- `parse(text: str) -> T`：将模型原始文本解析为目标结构。
- `get_format_instructions() -> str`：返回提示模板中用于指导模型输出的格式说明。
- `parse_with_prompt(text, prompt)`：部分解析器提供，用于结合提示信息进行纠错。

```python
from langchain_core.output_parsers import BaseOutputParser
```

（来源：https://github.com/langchain-ai/langchain）

## 使用 PydanticOutputParser

`PydanticOutputParser` 使用 Pydantic 模型定义目标结构，适合复杂数据。你可以在字段上添加 `description` 指导模型生成。

```python
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

class Recipe(BaseModel):
    name: str = Field(description="菜名")
    ingredients: list[str] = Field(description="原料列表")
    steps: list[str] = Field(description="步骤列表")

parser = PydanticOutputParser(pydantic_object=Recipe)
format_instructions = parser.get_format_instructions()
```

将 `format_instructions` 拼入提示词，模型会输出 JSON，随后 `parse` 会按 Pydantic 模型校验并生成 `Recipe` 实例。

（来源：https://github.com/langchain-ai/langchain）

## 使用 JsonOutputParser

`JsonOutputParser` 可以自动从输出中提取 JSON，不强制要求固定模式。它特别适合模型返回包含 Markdown 代码块围栏的 JSON 场景。

```python
from langchain_core.output_parsers import JsonOutputParser

parser = JsonOutputParser()
```

在提示词中可调用 `parser.get_format_instructions()` 让模型了解输出格式。

（来源：https://github.com/langchain-ai/langchain）

## 使用 with_structured_output

较新版本的 LangChain 聊天模型提供了 `with_structured_output()` 方法，可以直接绑定一个 Pydantic 类或 JSON Schema，简化调用流程：

```python
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

class Joke(BaseModel):
    setup: str
    punchline: str

model = ChatOpenAI(model="gpt-4o")
structured_llm = model.with_structured_output(Joke)
result = structured_llm.invoke("给我讲一个程序员的笑话")
```

这样返回的就是一个 `Joke` 实例，而不是字符串，省去了手动解析的步骤。

（来源：https://github.com/langchain-ai/langchain）

## 解析失败与错误恢复

模型输出不符合格式时，`parse` 方法通常会抛出 `OutputParserException`。LangChain 提供了几种恢复机制：

### OutputFixingParser

`OutputFixingParser` 包装另一个解析器，并在解析失败时调用一个修正模型，让模型尝试修复原始输出。

```python
from langchain.output_parsers import OutputFixingParser
from langchain_openai import ChatOpenAI

fixing_parser = OutputFixingParser(
    parser=parser,
    llm=ChatOpenAI()
)
```

### RetryOutputParser

`RetryOutputParser` 会在解析失败时，将原始输出和错误信息一并提供给模型，要求模型重新生成输出，然后再次解析。

```python
from langchain.output_parsers import RetryOutputParser
from langchain_core.prompts import PromptTemplate

retry_parser = RetryOutputParser(
    parser=parser,
    llm=ChatOpenAI()
)
prompt = PromptTemplate.from_template("请回答：{input}")
```

使用时需要传入原始 prompt 和输出：

```python
parsed = retry_parser.parse_with_prompt(
    text="无法解析的内容",
    prompt_value=prompt.format_prompt(input="问题")
)
```

### 在链中使用重试

可以将 `RetryOutputParser` 作为链的最后一个组件，实现自动重试：

```python
chain = prompt | llm | retry_parser
```

注意：`RetryOutputParser` 依赖 `parse_with_prompt` 获取 prompt 上下文，因此不能简单替代普通解析器。

（来源：https://github.com/langchain-ai/langchain）

## 实践建议

- 在提示词中明确要求输出 JSON，并使用 `get_format_instructions()` 提供具体格式。
- 对解析后的结果进行业务验证，避免依赖模型输出的持久性。
- 记录解析失败示例，用于后续提示工程迭代。

再次提醒：本教程内容基于 https://github.com/langchain-ai/langchain 的公开信息，无法从该 URL 直接验证所有细节。实际使用时请参考 LangChain 的官方文档对应版本。