# 使用多架构支持的 Debian bookworm 作为基础镜像
FROM debian:bookworm

# 声明多架构构建参数[4,6](@ref)
ARG TARGETARCH
ARG TARGETPLATFORM
ARG BUILDPLATFORM
ARG HTTP_PROXY
ARG HTTPS_PROXY

# 设置环境变量
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV TARGETARCH=$TARGETARCH

# 设置工作目录
WORKDIR /app

# 记录构建架构信息[7](@ref)
RUN echo "构建目标架构: $TARGETPLATFORM" > /build-info.txt && \
    echo "构建平台: $BUILDPLATFORM" >> /build-info.txt

# 1. 配置阿里云 apt 镜像源
RUN echo "deb http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian/ bookworm-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list

# 2. 更新系统并安装基础依赖（条件性代理设置）[7](@ref)
RUN if [ -n "$HTTP_PROXY" ]; then export http_proxy=$HTTP_PROXY; fi; \
    if [ -n "$HTTPS_PROXY" ]; then export https_proxy=$HTTPS_PROXY; fi; \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    wget \
    curl \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    pkg-config \
    net-tools \
    build-essential \
    gcc \
    g++ \
    make \
    portaudio19-dev \
    nginx \
    && rm -rf /var/lib/apt/lists/* && apt-get clean

# 创建nginx用户
RUN groupadd -r nginx && \
    useradd -r -g nginx nginx && \
    mkdir -p /var/log/nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx /var/cache/nginx

# 3. 创建 Python 符号链接
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

# 4. 安装 uv（条件性代理设置）[7](@ref)
RUN if [ -n "$HTTP_PROXY" ]; then export http_proxy=$HTTP_PROXY; fi; \
    if [ -n "$HTTPS_PROXY" ]; then export https_proxy=$HTTPS_PROXY; fi; \
    wget -qO- https://astral.sh/uv/install.sh | sh && \
    echo 'export PATH="/root/.cargo/bin:$PATH"' >> /root/.bashrc

# 5. 配置 uv 使用阿里云镜像
RUN mkdir -p /root/.config/uv && \
    echo '[[index]]' > /root/.config/uv/uv.toml && \
    echo 'url = "https://mirrors.aliyun.com/pypi/simple/"' >> /root/.config/uv/uv.toml && \
    echo 'default = true' >> /root/.config/uv/uv.toml

# 6. 复制项目代码到容器
COPY . /app

# 7. 设置环境变量
ENV PATH="/root/.cargo/bin:/usr/bin:/usr/local/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# 8. 复制 entrypoint 脚本
COPY ./docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 9. 创建数据持久化目录
VOLUME ["/data"]

# 10. 显示构建信息
RUN cat /build-info.txt

# 11. 设置入口点
ENTRYPOINT ["/entrypoint.sh"]
