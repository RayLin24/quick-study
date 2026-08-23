# 部署与最佳实践

> 说明：本教程的注册源为 LangChain 官方仓库（[https://github.com/langchain-ai/langchain](https://github.com/langchain-ai/langchain)），但我们没有该仓库的快照证据，因此本章无法从注册源核实具体的 API 签名、版本号与代码示例。以下内容基于通用实践编写，具体部署细节请以官方文档为准。

## 将 LangChain 应用封装为 API 服务

LangChain 应用通常是一个 `Chain` 或 `Agent` 对象，它接收输入并返回输出。要在生产环境中使用，最直接的方式是将其封装为 REST API，这样其他服务、前端或外部调用方可以通过 HTTP 访问。

推荐使用 **FastAPI** 这类现代 Web 框架来构建 API，它天然支持异步、类型校验和交互式文档。以下示例演示了如何将一个问答链封装成 `POST /ask` 端点（仅用于说明思路，并非来自注册源）：

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

app = FastAPI()
model = ChatOpenAI()
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("user", "{question}")
])
chain = prompt | model

class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str

@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    try:
        result = await chain.ainvoke({"question": request.question})
        return AskResponse(answer=result.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

注意：调用大模型通常是 I/O 密集操作，使用 `async` 可以提升并发吞吐量。如果你使用的是同步链，可以考虑在 `run_in_executor` 中执行，避免阻塞事件循环。

## 并发处理

生产环境必须面对多个用户同时请求的情况。并发处理的核心原则是：

- **无状态设计**：不要在服务实例内部保存用户会话数据，使得每个请求可以独立处理，方便水平扩展。
- **异步与线程池**：异步 Web 框架可以同时处理大量等待 I/O 的请求，而 CPU 密集的环节（如本地推理）则适合线程池或进程池。
- **控制并发上限**：对模型 API 的调用通常有速率限制，使用信号量（`asyncio.Semaphore`）或限流中间件防止超出配额。

示例：使用信号量限制对底层模型的并发调用。

```python
import asyncio
from fastapi import FastAPI

app = FastAPI()
semaphore = asyncio.Semaphore(10)

@app.post("/ask")
async def ask(request: AskRequest):
    async with semaphore:
        # 调用 chain
        result = await chain.ainvoke({"question": request.question})
    return {"answer": result.content}
```

## 安全

安全性是部署 API 服务时不可忽视的问题，主要有以下几个方面：

- **输入校验**：使用 Pydantic 模型定义请求体，避免恶意构造的输入导致异常或资源耗尽。
- **Prompt 注入防护**：用户输入可能包含试图覆盖系统指令的内容，需要对输入进行过滤或采用更安全的提示模板，必要时对用户输入做转义。
- **密钥管理**：不要在代码或环境变量中明文存储 API 密钥，使用密钥管理服务（如 Vault）或云平台的安全凭据管理功能。
- **身份认证与授权**：为 API 添加 API Key、OAuth2 或 JWT 等认证机制，限制只有合法用户才能访问。
- **速率限制（Rate Limiting）**：防止单个用户占用过多资源，或通过大量请求进行攻击。

以下是一个简单的 API Key 校验示例（示意）：

```python
from fastapi import FastAPI, Depends, HTTPException, Header

API_KEY = "your-secret-key"

def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key

@app.post("/ask", dependencies=[Depends(verify_api_key)])
async def ask(request: AskRequest):
    # ...
```

## 成本控制

LLM API 调用通常按 token 计费，且昂贵的模型成本更高。控制成本的手段包括：

- **缓存**：对相同或相似的请求结果进行缓存（如 Redis），避免重复调用模型。
- **选择合适模型**：对简单任务使用小模型或更便宜的模型，对复杂推理才使用大模型。
- **限制 token 数量**：设置 `max_tokens` 和提示词长度，避免生成过长内容。
- **批量处理**：如果业务允许，将多个请求合并为一个调用，减少请求数。
- **限流与配额**：为每个用户或客户端设置调用上限，防止意外高额账单。

## 扩展

当业务规模增长时，需要水平扩展服务实例。关键措施：

- **容器化**：使用 Docker 将应用打包，便于在 Kubernetes 等平台部署和伸缩。
- **无状态服务**：再次强调，不要在本地保存状态，这样任意实例都可以处理任意请求。
- **负载均衡**：通过负载均衡器分发请求到多个实例。
- **消息队列**：对耗时较长的任务，可以将其放入队列（如 Celery + Redis），异步处理并通知结果。

## 监控与日志

生产环境还需要可观测性：

- 记录每个请求的延迟、模型调用次数、错误信息和返回内容量。
- 使用结构化日志（JSON 格式）方便采集。
- 对接追踪系统（如 OpenTelemetry）以分析链的执行链路。
- 设置告警规则，当错误率或延迟超过阈值时及时通知。

## 结语

将 LangChain 应用封装为 API 服务只是第一步，真正上线还需要考虑并发、安全、成本和扩展。本章的示例均为简化演示，实际部署时请查阅 FastAPI、LangChain 及相关云服务的官方文档，结合业务场景做针对性设计。