# ============ 第 2 周学习项目 Dockerfile ============
# 镜像分层：每一行指令生成一个只读层，改哪层只重建那层之后的部分
# 所以"先拷贝 requirements 再拷贝代码"能让依赖层被缓存 —— 代码改了不用重新下载依赖

FROM python:3.12-slim

# 环境变量：不生成 __pycache__、输出不缓冲（日志实时可见）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1. 先只拷贝依赖清单并安装（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 再拷贝代码（.dockerignore 已排除 .venv/.git/.env 等）
COPY . .

# 运行时目录：日志、上传文件
RUN mkdir -p /data/uploads /app/logs

# 容器对外端口（只是声明，真正映射在 docker-compose 的 ports）
EXPOSE 8000

# 启动命令。注意：
# - 必须监听 0.0.0.0 而不是 127.0.0.1，否则容器外（宿主机）访问不到
# - 生产可加 --workers 4（多进程），但连接池参数要相应调小
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
