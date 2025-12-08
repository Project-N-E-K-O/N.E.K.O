# 使用 Debian bookworm 作为基础镜像
FROM debian:bookworm

# 设置环境变量
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 设置工作目录
WORKDIR /app

# 1. 配置阿里云 apt 镜像源
RUN echo "deb http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb-src http://mirrors.aliyun.com/debian/ bookworm-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list

# 2. 更新系统并安装基础依赖
# 注意：此阶段编译需要预填临时代理，否则编译速度会很慢，除非你在国外
RUN export http_proxy=http://192.168.8.2:10809 && \
    export https_proxy=http://192.168.8.2:10809 && \
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
    && rm -rf /var/lib/apt/lists/* && \
    unset http_proxy && \
    unset https_proxy

#创建nginx用户
RUN groupadd -r nginx && \
    useradd -r -g nginx nginx && \
    mkdir -p /var/log/nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx /var/cache/nginx

# 3. 创建 Python 符号链接
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

# 4. 安装 uv
# 注意：此阶段编译需要预填临时代理，否则编译速度会很慢，除非你在国外
RUN export http_proxy=http://192.168.8.2:10809 && \
    export https_proxy=http://192.168.8.2:10809 && \
    wget -qO- https://astral.sh/uv/install.sh | sh && \
    echo 'export PATH="/root/.cargo/bin:$PATH"' >> /root/.bashrc && \
    unset http_proxy && \
    unset https_proxy

# 5. 配置 uv 使用阿里云镜像
RUN mkdir -p /root/.config/uv && \
    echo '[[index]]' > /root/.config/uv/uv.toml && \
    echo 'url = "https://mirrors.aliyun.com/pypi/simple/"' >> /root/.config/uv/uv.toml && \
    echo 'default = true' >> /root/.config/uv/uv.toml

# 6. 克隆 N.E.K.O. 项目
# 注意：此阶段编译需要预填临时代理，否则编译速度会很慢，除非你在国外
RUN export http_proxy=http://192.168.8.2:10809 && \
    export https_proxy=http://192.168.8.2:10809 && \
    cd / && \
    git clone https://github.com/Project-N-E-K-O/N.E.K.O.git app && \
    cd /app && \
    git checkout main && \
    unset http_proxy && \
    unset https_proxy

# 7. 设置环境变量
ENV PATH="/root/.cargo/bin:/usr/bin:/usr/local/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# 8. 复制 entrypoint 脚本
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 9. 创建数据持久化目录
VOLUME ["/data"]

# 10. 设置入口点
ENTRYPOINT ["/entrypoint.sh"]
