# quickstudy

把任何官方技术文档站变成一本面向初学者的中文学习手册（代码示例经无网络沙箱验证可运行）。

## 快速开始（Web 界面）

```powershell
conda env create -f environment.yml   # Python 3.12，pip 自动安装全部后端依赖
conda activate quickstudy
pip install -e .                      # 若用 environment.lock.yml 精确复现，需补这步装项目本体
playwright install chromium           # 仅 JS 渲染站需要
cd web; npm install; npm run build; cd ..
# 配置环境变量（见 .env.example）：ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 必填
quickstudy serve                      # 打开 http://127.0.0.1:8600
```

依赖文件：`environment.yml`（声明式，跟随 pyproject 解析最新兼容版本）；
`environment.lock.yml`（`conda env export` 生成的精确锁定版，复现本机环境用）。

输入文档站地址 → 自动爬取/组织/Demo/大纲 → **确认大纲后才消耗写作 token** → 在线阅读。

## CLI 模式

`quickstudy crawl|organize|demos|outline|book <url>`，详见 README.dev.md。

## 前置依赖

Miniconda/Anaconda（后端环境）、Node.js（前端构建）、Docker Desktop（仅 Demo 沙箱，未启动自动跳过）。
