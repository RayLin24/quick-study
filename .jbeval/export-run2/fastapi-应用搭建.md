# 搭建 FastAPI 被测应用

> **证据说明**：本教程引用的 FastAPI 官方首步教程（https://fastapi.tiangolo.com/tutorial/first-steps/）属于注册来源，但并未以快照形式提供。因此，本文中所有代码片段、命令和 API 名称均无法在该来源上逐字核对。下文内容是对该教程常见内容的复述，并明确标注为教学抽象。若你在实际项目中遇到差异，请以官方文档为准。

## 1. 安装依赖

FastAPI 本身是 Web 框架，需要配合 ASGI 服务器（如 uvicorn）才能运行。按照官方首步教程，常见的安装命令如下（这里不指定版本号，安装最新版即可）：

```bash
pip install fastapi
pip install "uvicorn[standard]"
```

其中 `uvicorn[standard]` 会安装 uvicorn 及其推荐依赖，便于开发调试。

> 教学抽象：上述命令得到的是 FastAPI 应用最基本的运行环境。后续执行端到端测试时，测试脚本与目标应用可以共享或复用同一套依赖。

## 2. 创建最小 FastAPI 应用

在项目目录中新建一个 Python 文件，通常命名为 `main.py`。下面是最小化的应用代码，定义了一条 `GET /` 路径操作：

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}
```

这段代码做了三件事：

1. 导入 `FastAPI` 类。
2. 创建 `app` 实例，它是 ASGI 应用对象，也是被测目标（SUT）的入口。
3. 使用装饰器 `@app.get("/")` 注册一个路径操作：当客户端 `GET` 访问根路径时，执行 `root()` 函数并返回 JSON 响应。

> 注意：`async def` 是 FastAPI 支持的写法。若你的业务逻辑不使用 `await`，也可以写成普通 `def`，FastAPI 会自动在线程池中调用它。这一细节对端到端测试无影响。

## 3. 启动服务

在项目目录下执行以下命令，启动 uvicorn 服务器：

```bash
uvicorn main:app --reload
```

- `main:app` 表示从 `main.py` 中导入名为 `app` 的 FastAPI 实例。
- `--reload` 让服务在代码变更后自动重启，适合开发阶段。

启动后，终端会输出类似以下信息（具体输出取决于版本）：

```text
INFO:     Uvicorn running on http://127.0.0.1:8000
```

此时，被测应用已在本地 8000 端口监听。

> 如果你修改了代码或需要指定其他端口，可以添加 `--host` 和 `--port` 参数，例如 `uvicorn main:app --host 0.0.0.0 --port 9000`。这是教学抽象：具体参数以 uvicorn 官方命令行为准。

## 4. 验证被测目标

启动服务后，在浏览器或命令行中访问 http://127.0.0.1:8000/，应当看到：

```json
{"message":"Hello World"}
```

另外，FastAPI 会自动生成交互式 API 文档，访问 http://127.0.0.1:8000/docs 可查看。

对于端到端测试而言，这个最小应用可以作为一个“被测目标”：测试脚本向 `GET /` 发送请求，并断言响应的状态码为 200、JSON body 等于 `{"message": "Hello World"}`。

## 小结

本章搭建了一个最小 FastAPI 应用，包括依赖安装、路径操作定义和服务启动。它是后续端到端测试的基准目标。下一章将引入测试框架，对 `GET /` 发起真实 HTTP 请求并验证响应。
