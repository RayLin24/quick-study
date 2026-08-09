# quickstudy

把任何官方技术文档站变成一本面向初学者的中文学习手册（代码示例经无网络沙箱验证可运行）。

## 快速开始（Web 界面）

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev,m2,render,web]"
playwright install chromium        # 仅 JS 渲染站需要
cd web; npm install; npm run build; cd ..
# 配置环境变量（见 .env.example）：ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 必填
.venv\Scripts\quickstudy.exe serve  # 打开 http://127.0.0.1:8600
```

输入文档站地址 → 自动爬取/组织/Demo/大纲 → **确认大纲后才消耗写作 token** → 在线阅读。

## CLI 模式

`quickstudy crawl|organize|demos|outline|book <url>`，详见 README.dev.md。

## 前置依赖

Python ≥3.11、Node.js（前端构建）、Docker Desktop（仅 Demo 沙箱，未启动自动跳过）。
