# quickstudy

把任何官方技术文档站变成一本面向初学者的中文学习手册（代码示例经无网络沙箱验证可运行）。

## 快速开始（Web 界面）

```powershell
# 方式一：environment.yml（声明式，跟随 pyproject 解析）
conda env create -f environment.yml

# 方式二：requirements.txt（pip 精确锁定，复现本机环境）
conda create -n quickstudy python=3.12 -y

conda activate quickstudy
pip install -r requirements.txt       # 方式一可跳过（已含）
pip install -e .                      # 安装项目本体（可编辑模式）
playwright install chromium           # 仅 JS 渲染站需要
cd web; npm install; npm run build; cd ..
# 配置环境变量（见 .env.example）：ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 必填
quickstudy serve                      # 打开 http://127.0.0.1:8600
```

依赖文件：

- `environment.yml` —— 声明式（Python 3.12 + pyproject extras）
- `environment.lock.yml` —— `conda env export -n quickstudy --no-builds` 全量锁定（conda+pip）
- `requirements.txt` —— `pip freeze` 纯 pip 锁定；再生成：
  `pip freeze | Select-String -NotMatch 'quickstudy' > requirements.txt`
  （剔除项目自身行——editable 安装会被 freeze 成远端 Git 地址，别的机器上无法安装）

输入文档站地址 → 自动爬取/组织/Demo/大纲 → **确认大纲后才消耗写作 token** → 在线阅读。

## CLI 模式

`quickstudy crawl|organize|demos|outline|book <url>`，详见 README.dev.md。

## 前置依赖

Miniconda/Anaconda（后端环境）、Node.js（前端构建）、Docker Desktop（仅 Demo 沙箱，未启动自动跳过）。
