# 多阶段构建 Dockerfile for N.E.K.O. Project

# 阶段 1: 基础镜像
FROM python:3.11-slim as base

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libsndfile1 \
    libportaudio2 portaudio19-dev \
    libgomp1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# 阶段 2: 依赖安装
FROM base as dependencies

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 阶段 3: 应用构建
FROM base as application

# 从依赖阶段复制已安装的包
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# 复制应用代码
COPY . /app

# 创建必要的目录
RUN mkdir -p /app/config \
    /app/memory/store \
    /app/logs \
    /app/static

# 设置权限
RUN chmod +x /app/docker/entrypoint.sh 2>/dev/null || true

# 暴露端口
EXPOSE 48911 48912 48913 48915

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    TZ=Asia/Shanghai

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:48911/ || exit 1

# 启动脚本
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "main_server.py"]

