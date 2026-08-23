# 工具与代理（Tools and Agents）

## 阅读说明

本章的内容目标是：定义可被模型调用的工具，并构建代理（agent），让模型能够自主规划、调用工具并迭代完成复杂任务。

本教程以 [langchain-ai/langchain](https://github.com/langchain-ai/langchain) 为注册参考来源。本次没有附上该仓库的快照证据，因此我无法核实具体 API 名称、参数、导入路径或版本号。**下文所有代码都是教学抽象**，用于解释概念，不能直接当作 LangChain 当前 API 的权威用法；请以官方源码和官方文档为准。

## 为什么需要工具与代理？

大语言模型本身只会生成文本，无法可靠地查数据库、调用计算器、访问网络或执行外部动作。工具（tool）把外部能力封装成模型可以“请求调用”的接口；代理（agent）则让模型在多个工具之间做选择，并根据返回结果继续规划，直到任务完成。

简化的分工是：

- 工具负责“执行”；
- 模型负责“决策”；
- 代理负责把两者循环起来。

## 工具：把能力暴露给模型

一个工具通常由四个部分组成：

1. 名称（name）：机器可读的唯一标识。
2. 描述（description）：说明工具何时使用、如何使用。
3. 参数定义（parameters）：通常是 JSON Schema，说明输入参数的结构。
4. 执行函数（func）：真正去执行任务并返回结果的代码。

模型不会直接运行这个函数，而是返回一个结构化的调用请求；由你的程序去调用函数，并把结果送回给模型。下面是一个教学用的极简工具类：

```python
class Tool:
    '''教学抽象：表示一个可被模型调用的工具。'''

    def __init__(self, name, description, parameters, func):
        self.name = name
        self.description = description
        self.parameters = parameters  # JSON Schema 风格的参数定义
        self.func = func

    def invoke(self, **kwargs):
        # 教学抽象：真实框架还会处理参数校验、错误处理、日志等
        return self.func(**kwargs)


def get_current_weather(location: str) -> str:
    '''返回指定城市当前天气概况（教学示例）。'''
    return f'{location}：晴，23℃'


get_weather_tool = Tool(
    name='get_current_weather',
    description='返回指定城市当前天气概况。当用户询问天气时使用。',
    parameters={
        'type': 'object',
        'properties': {
            'location': {
                'type': 'string',
                'description': '城市名'
            }
        },
        'required': ['location'],
    },
    func=get_current_weather,
)
```

当模型接收到这份参数定义后，它可能返回这样的调用请求：

```python
tool_calls = [
    {
        'id': 'call_123',
        'name': 'get_current_weather',
        'args': {'location': '上海'},
    }
]
```

在 LangChain 生态中，类似的工具抽象往往通过装饰器（例如 `@tool`）或工具类自动生成。由于没有快照证据，这里不给出具体的导入路径和装饰器用法；你需要到官方仓库核对最新的 API。

## 代理：规划、调用、观察的循环

代理（agent）不是一个特殊的模型，而是一种程序结构。它的核心循环是：

1. 把当前对话和工具定义交给模型；
2. 模型决定“直接回答”还是“调用某个工具”；
3. 如果调用工具，程序执行该工具；
4. 把工具结果追加到对话上下文中；
5. 回到第 1 步，直到模型认为任务完成。

下面是最小代理循环的教学实现：

```python
def run_agent(model, tools, user_input, max_steps=5):
    '''教学抽象：最小代理循环，仅用于解释概念。'''
    tool_by_name = {tool.name: tool for tool in tools}
    messages = [
        {'role': 'system', 'content': '你是一个可以调用工具的助手。'},
        {'role': 'user', 'content': user_input},
    ]

    for step in range(max_steps):
        # 1) 模型根据当前消息和工具定义做出选择
        response = model.chat(messages, tools=[t.parameters for t in tools])

        # 2) 没有 tool_calls，说明模型准备直接回答
        if not response.tool_calls:
            return response.content

        # 3) 执行所有工具调用
        for call in response.tool_calls:
            tool = tool_by_name[call.name]
            result = tool.invoke(**call.args)

            # 4) 把工具结果放回上下文，模型才能“观察”到结果
            messages.append({
                'role': 'tool',
                'tool_call_id': call.id,
                'content': str(result),
            })

    raise RuntimeError(f'代理在 {max_steps} 步内没有完成任务')
```

这个循环之所以有效，是因为每一步的工具结果都会变成上下文的一部分。模型可以读到这些结果，再决定下一步做什么。例如：

1. 用户问：“上海明天适合跑步吗？”
2. 模型要求调用 `get_current_weather(location='上海')`；
3. 程序返回“晴，23℃”；
4. 模型看到天气后，再结合知识给出“适合跑步”的回答。

## 工具的常见设计原则

以下原则属于通用的工程经验，不是来自已核实证据，请把它当作教学建议：

- **描述要明确**：模型的决策很大程度上依赖工具描述；描述越清楚，工具被正确使用的概率越高。
- **参数尽量少且严格**：参数越少，模型理解成本越低；用 JSON Schema 约束类型和必填项。
- **返回可读的结果**：工具结果会被模型当作文本阅读，尽量返回结构清晰、包含必要信息的字符串或结构化数据。
- **错误也要返回，而不是抛出异常**：把错误信息作为结果返回给模型，模型才能尝试其他工具或向用户解释。
- **敏感操作要加权限**：不要让模型在未经确认的情况下执行删除、转账等高风险动作。

## 什么时候用代理？

代理适合那些“步骤不固定”的任务，例如：根据订单号查询物流、计算价格、再查天气后生成出行建议。

如果任务的步骤是固定的，用普通链式调用比代理更稳定、更便宜、更可控。代理会引入额外延迟、模型决策错误和更多的 token 消耗。

## 思考问题

1. 如果工具描述写得含糊，模型调用它时会发生什么？
2. 在代理循环中，工具结果应该以什么形式放回上下文？
3. 什么情况下应该限制代理的最大步数？
4. 固定流程任务为什么不一定适合用代理？

## 小结

工具把“执行能力”提供给模型；代理把“决策能力”组织成一个循环。两者结合，模型才能自主规划、调用工具并迭代完成复杂任务。

再次提醒：本章代码为教学抽象，且没有对应的快照证据可核对。具体到 LangChain 的实际代码，请打开 [langchain-ai/langchain](https://github.com/langchain-ai/langchain) 仓库，查阅最新源码与文档。