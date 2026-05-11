# ============================================================================
# N.E.K.O. Dockerfile - Standard Version (lightweight, downloads Chromium at runtime)
# ============================================================================
# This Dockerfile builds the "standard" version of N.E.K.O (~1.5GB).
# Chromium browser is downloaded on first container start.
#
# Build: docker build -f docker/Dockerfile -t neko:latest .
# Run:   docker run -d --name neko -p 48911:80 -p 48912:443 neko:latest
#
# Usage with docker-compose:
#   docker-compose up -d  (uses latest tag by default)
# ============================================================================

# --- Stage 1: Build frontend projects ---
FROM node:24-slim AS frontend-builder
ARG HTTP_PROXY
ARG HTTPS_PROXY
WORKDIR /src

# Copy frontend source code from local build context
COPY frontend/plugin-manager /src/frontend/plugin-manager
COPY frontend/react-neko-chat /src/frontend/react-neko-chat

# Shared static assets bundled into plugin-manager at build time.
# yui-guide-runtime.ts imports tutorial PNGs and shared icons via
# `../../../static/...`; keep these COPY paths in sync with that file
# (currently: static/assets/tutorial/{ghost-cursor,highlight}, static/icons).
COPY static/assets/tutorial /src/static/assets/tutorial
COPY static/icons /src/static/icons

WORKDIR /src/frontend/plugin-manager
RUN npm ci && npm run build-only

WORKDIR /src/frontend/react-neko-chat
RUN npm ci && npm run build

# --- Stage 2: Runtime ---
FROM debian:bookworm
ARG HTTP_PROXY
ARG HTTPS_PROXY

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# Set working directory
WORKDIR /app

# 1. Configure Aliyun apt mirror sources (HTTPS)
RUN echo "deb https://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb-src https://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb-src https://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb-src https://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.aliyun.com/debian/ bookworm-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list && \
    echo "deb-src https://mirrors.aliyun.com/debian/ bookworm-backports main non-free non-free-firmware contrib" >> /etc/apt/sources.list

# 2. Update system and install base dependencies
RUN if [ -n "$HTTP_PROXY" ]; then export http_proxy=$HTTP_PROXY; fi; \
    if [ -n "$HTTPS_PROXY" ]; then export https_proxy=$HTTPS_PROXY; fi; \
    apt-get update && \
    apt-get install -y --no-install-recommends \
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
    openssl \
    bc \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* && \
    unset http_proxy && \
    unset https_proxy

# Create nginx user and neko application user, plus Playwright cache directory
RUN groupadd -r nginx && \
    useradd -r -g nginx nginx && \
    mkdir -p /var/log/nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx /var/cache/nginx && \
    groupadd -r neko && \
    useradd -r -g neko -d /app -s /bin/bash neko && \
    mkdir -p /app/logs /app/N.E.K.O /root/ssl /opt/ms-playwright && \
    chown -R neko:neko /app /opt/ms-playwright

# 3. Create Python symlinks
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

# 4. Install uv from official Docker image (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.6.17 /uv /usr/local/bin/uv
RUN chmod +x /usr/local/bin/uv && \
    echo 'export PATH="/usr/local/bin:$PATH"' >> /root/.bashrc

# 5. Configure uv to use Aliyun mirror
RUN mkdir -p /root/.config/uv && \
    echo '[[index]]' > /root/.config/uv/uv.toml && \
    echo 'url = "https://mirrors.aliyun.com/pypi/simple/"' >> /root/.config/uv/uv.toml && \
    echo 'default = true' >> /root/.config/uv/uv.toml

# 6. Copy N.E.K.O. project from local build context (with correct ownership)
COPY --chown=neko:neko . /app

# 7. Copy frontend build artifacts
# Note: react-neko-chat's vite.config.ts writes to outDir `../../static/react/neko-chat`
# (relative to its package root), so the actual build output lives at
# /src/static/react/neko-chat — NOT /src/frontend/react-neko-chat/dist.
COPY --from=frontend-builder /src/frontend/plugin-manager/dist /app/frontend/plugin-manager/dist
COPY --from=frontend-builder /src/static/react/neko-chat /app/static/react/neko-chat

# 7b. Extract yui-origin Live2D model (assets/yui-origin.tar.gz → static/yui-origin/)
RUN cd /app && tar -xzmf assets/yui-origin.tar.gz -C static/ && \
    test -f static/yui-origin/yui-origin.moc3

# 8. Install Python dependencies using uv
RUN cd /app && \
    if [ -n "$HTTP_PROXY" ]; then export http_proxy=$HTTP_PROXY; fi; \
    if [ -n "$HTTPS_PROXY" ]; then export https_proxy=$HTTPS_PROXY; fi; \
    uv sync --frozen && \
    unset http_proxy && \
    unset https_proxy

# 8b. Fix ownership after root operations (uv sync creates files as root)
RUN chown -R neko:neko /app

# 9. Set Playwright environment variable
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# 10. Set remaining environment variables
ENV PATH="/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# 11. Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 12. Create data persistence directory
VOLUME ["/data"]

# 13. Expose ports
EXPOSE 80 443

# 14. Show build information
RUN echo "========================================" && \
    echo "  N.E.K.O. Standard Image Build Complete" && \
    echo "========================================" && \
    echo "Python version: $(python --version)" && \
    echo "UV version: $(uv --version)" && \
    echo "Nginx version: $(nginx -v 2>&1)" && \
    echo "========================================"

# 15. Set entrypoint
ENTRYPOINT ["/entrypoint.sh"]
