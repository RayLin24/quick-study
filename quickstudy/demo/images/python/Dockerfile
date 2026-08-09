# 沙箱镜像（python 系）：依赖在构建期安装完毕，运行时 --network none。
# 版本锚定（design.md 6.3.3）：按目标文档版本钉住主依赖，避免"文档新版/代码老API"错配。
FROM python:3.11-slim

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1" \
    "pydantic>=2,<3" \
    "httpx>=0.27" \
    "pytest>=8" \
    "uvicorn>=0.30"

WORKDIR /work
