#!/bin/bash
set -e

# N.E.K.O. Docker Entrypoint Script with Nginx Reverse Proxy
PIDS=()

# 设置环境变量
export NEKO_MAIN_SERVER_PORT=${NEKO_MAIN_SERVER_PORT:-48911}
export NGINX_PORT=${NGINX_PORT:-80}
export NGINX_SSL_PORT=${NGINX_SSL_PORT:-443}

# 1. 信号处理优化
setup_signal_handlers() {
    trap 'echo "🛑 Received shutdown signal"; nginx -s stop 2>/dev/null || true; for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done; wait; exit 0' TERM INT
}

# 2. 环境检查与初始化优化
check_dependencies() {
    echo "🔍 Checking system dependencies..."
    
    # 确保完整的PATH设置
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/root/.cargo/bin:$PATH"
    
    # 检查Python可用性
    if ! command -v python &> /dev/null; then
        echo "⚠️ Python3 not found. Installing python3.11..."
        apt-get update && apt-get install -y --no-install-recommends python3.11
    fi
    
    # 检查uv可用性
    if ! command -v uv &> /dev/null; then
        echo "⚠️ uv not found. Installing uv via official script..."
        
        # 使用官方安装脚本并指定安装位置
        wget -LsSf https://astral.sh/uv/install.sh | sh -s -- --install-dir /usr/local/bin
        
        # 确保安装目录在PATH中
        export PATH="/usr/local/bin:$PATH"
        
        # 验证安装
        if ! command -v uv &> /dev/null; then
            echo "❌ Failed to install uv. Attempting manual installation..."
            exit 1
        fi
    fi
    
    # 检查Nginx可用性
    if ! command -v nginx &> /dev/null; then
        echo "⚠️ Nginx not found. Installing nginx..."
        apt-get update && apt-get install -y --no-install-recommends nginx
    fi
    
    echo "✅ Dependencies checked:"
    echo "   UV version: $(uv --version)"
    echo "   Python version: $(python3 --version)"
    echo "   Nginx version: $(nginx -v 2>&1)"
}

# setup_nginx_proxy sets up and writes the Nginx main and site configuration for the container, creating proxy rules (including WebSocket support), static file serving, a health endpoint, removes the client request body size limit, and validates the resulting configuration.
setup_nginx_proxy() {
    echo "🌐 Setting up Nginx reverse proxy..."
    
    # 创建必要的日志目录
    mkdir -p /var/log/nginx
    
    # 生成SSL证书和密钥（如果不存在）
    echo "🔐 Setting up SSL certificates..."
    mkdir -p /root/ssl
    
    # 检查证书文件是否存在，不存在则创建
    if [ ! -f "/root/ssl/N.E.K.O.crt" ] || [ ! -f "/root/ssl/N.E.K.O.key" ]; then
        echo "🔐 Creating SSL certificate and key..."
        
        # 创建证书文件
        cat > /root/ssl/N.E.K.O.crt << 'CERT_EOF'
-----BEGIN CERTIFICATE-----
MIIB/jCCAaOgAwIBAgIEabUgwjAKBggqhkjOPQQDAjBGMQswCQYDVQQGEwJDTjEV
MBMGA1UEChMMcHJvamVjdC1uZWtvMQkwBwYDVQQLEwAxFTATBgNVBAMTDHByb2pl
Y3QtbmVrbzAgFw0yNjAzMTQwODQ3NTlaGA8zMDI2MDMxNDA4NDc1OVowTTELMAkG
A1UEBhMCQ04xFTATBgNVBAoTDHByb2plY3QtbmVrbzEJMAcGA1UECxMAMRwwGgYD
VQQDExNwcm9qZWN0LW5la28ub25saW5lMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcD
QgAEhK6/3L/MGdFLScvHKApBqFIyiWH/bicnsACNFgMvLQXv8KeAkWvxSktHr4aW
CyegY2S3aRIivUdaSb3Fr00c96N2MHQwDgYDVR0PAQH/BAQDAgWgMBMGA1UdJQQM
MAoGCCsGAQUFBwMBMAwGA1UdEwEB/wQCMAAwHwYDVR0jBBgwFoAUN3UVZipumRTY
tpa1Nrr0rtGctIYwHgYDVR0RBBcwFYITcHJvamVjdC1uZWtvLm9ubGluZTAKBggq
hkjOPQQDAgNJADBGAiEAzLNiH6T5kLlCN2ZeatDad4WvRRUfST99QALuQifKOa0C
IQD9nENFTnT3MFMFUNVJO8IrKS8ji2kZdr73TEoWYRGilQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIBxDCCAWmgAwIBAgIEabUgqjAKBggqhkjOPQQDAjBGMQswCQYDVQQGEwJDTjEV
MBMGA1UEChMMcHJvamVjdC1uZWtvMQkwBwYDVQQLEwAxFTATBgNVBAMTDHByb2pl
Y3QtbmVrbzAeFw0yNjAzMTQwODQ3MzdaFw0zNjAzMTQwODQ3MzdaMEYxCzAJBgNV
BAYTAkNOMRUwEwYDVQQKEwxwcm9qZWN0LW5la28xCTAHBgNVBAsTADEVMBMGA1UE
AxMMcHJvamVjdC1uZWtvMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEkvfDNBpS
3PSdsK0lglxoZqWkzJDHMaEK2yOG5zn87NACPVPhZAAixkQT3Mji85B3gxWoRThw
WrAXdwDwCuRgFKNFMEMwDgYDVR0PAQH/BAQDAgEGMBIGA1UdEwEB/wQIMAYBAf8C
AQEwHQYDVR0OBBYEFIiH1k6I01mq3oKfyoL0Mp5wOzoMMAoGCCqGSM49BAMCA0kA
MEYCIQDZCy064fs9ZbHnRUjfhH/6yM/Sj/84tB+eSbfN6/jKNAIhAIidL5oONGel
Syk/YH+I7407Bh1hjhv0K+Izbn9mIpy1
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIBxTCCAWugAwIBAgIEabUgwTAKBggqhkjOPQQDAjBGMQswCQYDVQQGEwJDTjEV
MBMGA1UEChMMcHJvamVjdC1uZWtvMQkwBwYDVQQLEwAxFTATBgNVBAMTDHByb2pl
Y3QtbmVrbzAgFw0yNjAzMTQwODQ3NTlaGA8zMDI2MDMxNDA4NDc1OVowRjELMAkG
A1UEBhMCQ04xFTATBgNVBAoTDHByb2plY3QtbmVrbzEJMAcGA1UECxMAMRUwEwYD
VQQDEwxwcm9qZWN0LW5la28wWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQj7NZg
oVIAJgN/bEssc0JMfflTJYgu5DZosU5uRYzMpVIqwewv7oSmxp1koBpdy9DPzEyT
tr12BbAQPPJAlxuno0UwQzAOBgNVHQ8BAf8EBAMCAQYwEgYDVR0TAQH/BAgwBgEB
/wIBADAdBgNVHQ4EFgQUN3UVZipumRTYtpa1Nrr0rtGctIYwCgYIKoZIzj0EAwID
SAAwRQIgSVbDJVkSLF2Bg8N/520dayaqVteXvTR6uhdB0uHFMAsCIQDnHnIJkJAt
vJXs+nA+CcBi7iZ0PkJ/+MyX9vtHkTr/TA==
-----END CERTIFICATE-----
CERT_EOF
        
        # 创建私钥文件
        cat > /root/ssl/N.E.K.O.key << 'KEY_EOF'
-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIBj0hO3G8V9P67oBHvXhDKjDkU/d7BWSXvcLSA1QIpWVoAoGCCqGSM49
AwEHoUQDQgAEhK6/3L/MGdFLScvHKApBqFIyiWH/bicnsACNFgMvLQXv8KeAkWvx
SktHr4aWCyegY2S3aRIivUdaSb3Fr00c9w==
-----END EC PRIVATE KEY-----
KEY_EOF
        
        echo "✅ SSL certificate and key created"
    else
        echo "🔐 Using existing SSL certificate and key"
    fi
    
    # 设置SSL文件权限
    chmod 600 /root/ssl/N.E.K.O.key
    chmod 644 /root/ssl/N.E.K.O.crt
    
    # 生成主要的Nginx配置文件
    cat > /etc/nginx/nginx.conf <<EOF
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    log_format main '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                    '\$status \$body_bytes_sent "\$http_referer" '
                    '"\$http_user_agent" "\$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    
    sendfile on;
    tcp_nopush on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    
    # 包含我们的代理配置
    include /etc/nginx/conf.d/*.conf;
}
EOF
    
    # 生成合并的N.E.K.O代理配置（同时监听80和443端口）
    cat > /etc/nginx/conf.d/neko-proxy.conf <<EOF
server {
    listen ${NGINX_PORT};
    listen ${NGINX_SSL_PORT} ssl http2;
    
    # SSL证书配置（仅对443端口生效）
    ssl_certificate /root/ssl/N.E.K.O.crt;
    ssl_certificate_key /root/ssl/N.E.K.O.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    
    server_name _;
    
    # 禁用默认的Nginx版本显示
    server_tokens off;
    
    # 取消客户端请求体大小限制
    client_max_body_size 0;

    # 代理到N.E.K.O主服务
    location / {
        proxy_pass http://127.0.0.1:${NEKO_MAIN_SERVER_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 86400;  # 长超时用于WebSocket
    }
    
    # 代理到记忆服务
    location /memory/ {
        proxy_pass http://127.0.0.1:48912;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    # 代理到Agent服务
    location /agent/ {
        proxy_pass http://127.0.0.1:48915;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    # 静态文件服务
    location /static/ {
        alias /app/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        try_files \$uri \$uri/ =404;
    }
    
    # 健康检查端点
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
    
    # 阻止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF
    
    # 测试Nginx配置
    echo "🔧 Testing Nginx configuration..."
    if nginx -t; then
        echo "✅ Nginx configuration is valid"
    else
        echo "❌ Nginx configuration test failed"
        # 显示详细的错误信息
        nginx -t 2>&1
        exit 1
    fi
}

# 4. 配置管理优化
setup_configuration() {
    echo "📝 Setting up configuration..."
    local CONFIG_DIR="/app/config"
    local CORE_CONFIG_FILE="$CONFIG_DIR/core_config.json"
    
    mkdir -p "$CONFIG_DIR"
    
    # 只有在配置文件不存在或强制更新时才生成
    if [ ! -f "$CORE_CONFIG_FILE" ] || [ -n "${NEKO_FORCE_ENV_UPDATE}" ]; then
        cat > "$CORE_CONFIG_FILE" <<EOF
{
  "coreApiKey": "${NEKO_CORE_API_KEY:-}",
  "coreApi": "${NEKO_CORE_API:-qwen}",
  "assistApi": "${NEKO_ASSIST_API:-qwen}",
  "assistApiKeyQwen": "${NEKO_ASSIST_API_KEY_QWEN:-}",
  "assistApiKeyOpenai": "${NEKO_ASSIST_API_KEY_OPENAI:-}",
  "assistApiKeyGlm": "${NEKO_ASSIST_API_KEY_GLM:-}",
  "assistApiKeyStep": "${NEKO_ASSIST_API_KEY_STEP:-}",
  "assistApiKeySilicon": "${NEKO_ASSIST_API_KEY_SILICON:-}",
  "mcpToken": "${NEKO_MCP_TOKEN:-}"
}
EOF
        echo "✅ Configuration file created/updated"
    else
        echo "📄 Using existing configuration"
    fi
    
    # 安全显示配置（隐藏敏感信息）
    echo "🔧 Runtime Configuration:"
    echo "   Core API: ${NEKO_CORE_API:-qwen}"
    echo "   Assist API: ${NEKO_ASSIST_API:-qwen}"
    echo "   Main Server Port: ${NEKO_MAIN_SERVER_PORT:-48911}"
    echo "   Nginx HTTP Port: ${NGINX_PORT}"
    echo "   Nginx HTTPS Port: ${NGINX_SSL_PORT}"
}

# 6. 依赖管理优化
setup_dependencies() {
    echo "📦 Setting up dependencies..."
    cd /app
    
    # 激活虚拟环境（如果存在）
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    fi
    
    # 使用uv sync安装依赖
    echo "   Installing Python dependencies using uv..."
    
    # 检查是否存在uv.lock
    if [ -f "uv.lock" ]; then
        uv sync
    else
        # 如果没有锁定文件，尝试初始化
        if [ -f "pyproject.toml" ]; then
            uv sync
        else
            echo "⚠️ No pyproject.toml found. Initializing project..."
            uv init --non-interactive
            uv sync
        fi
    fi
    
    echo "✅ Dependencies installed successfully"
}

# 7. 服务启动优化
start_services() {
    echo "🚀 Starting N.E.K.O. services..."
    cd /app
    
    local services=("memory_server.py" "main_server.py" "agent_server.py")
    
    for service in "${services[@]}"; do
        if [ ! -f "$service" ]; then
            echo "❌ Service file $service not found!"
            # 对关键服务直接失败
            if [[ "$service" == "main_server.py" ]] || [[ "$service" == "memory_server.py" ]]; then
                return 1
            fi
            continue
        fi
        
        echo "   Starting $service..."
        # 启动服务并记录PID
        python "$service" &
        local pid=$!
        PIDS+=("$pid")
        echo "     Started $service with PID: $pid"
        sleep 5  # 给服务启动留出更多时间
    done
    
    # 健康检查
    echo "🔍 Performing health checks..."
    sleep 15
    
    # 检查进程是否运行
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "✅ Process $pid is running"
        else
            echo "❌ Process $pid failed to start"
            return 1
        fi
    done
    
    # 检查主服务端口（内部检查）
    if command -v ss &> /dev/null; then
        if ss -tuln | grep -q ":${NEKO_MAIN_SERVER_PORT} "; then
            echo "✅ Main server is listening on port ${NEKO_MAIN_SERVER_PORT}"
        else
            echo "❌ Main server failed to bind to port"
            return 1
        fi
    else
        echo "⚠️ Port check skipped (ss command not available)"
    fi
    
    echo "🎉 All N.E.K.O services started successfully!"
}

# 8. 启动Nginx代理
start_nginx_proxy() {
    echo "🌐 Starting Nginx reverse proxy..."
    
    # 启动Nginx
    nginx -g "daemon off;" &
    local nginx_pid=$!
    PIDS+=("$nginx_pid")
    
    sleep 3
    
    # 检查Nginx是否运行
    if kill -0 "$nginx_pid" 2>/dev/null; then
        echo "✅ Nginx is running with PID: $nginx_pid"
    else
        echo "❌ Nginx failed to start"
        return 1
    fi
    
    # 检查Nginx端口
    if command -v ss &> /dev/null; then
        echo "🔌 Checking HTTP port (${NGINX_PORT})..."
        if ss -tuln | grep -q ":${NGINX_PORT} "; then
            echo "✅ Nginx is listening on HTTP port ${NGINX_PORT}"
        else
            echo "❌ Nginx failed to bind to HTTP port ${NGINX_PORT}"
            return 1
        fi
        
        echo "🔌 Checking HTTPS port (${NGINX_SSL_PORT})..."
        if ss -tuln | grep -q ":${NGINX_SSL_PORT} "; then
            echo "✅ Nginx is listening on HTTPS port ${NGINX_SSL_PORT}"
        else
            echo "❌ Nginx failed to bind to HTTPS port ${NGINX_SSL_PORT}"
            return 1
        fi
    fi
    
    echo "🌐 Nginx proxy accessible at:"
    echo "   HTTP: http://localhost:${NGINX_PORT}"
    echo "   HTTPS: https://localhost:${NGINX_SSL_PORT}"
    echo "📊 Original service at: http://127.0.0.1:${NEKO_MAIN_SERVER_PORT}"
}

# 9. 主执行流程
main() {
    echo "=================================================="
    echo "   N.E.K.O. Container with Nginx Proxy - Startup"
    echo "=================================================="
    
    setup_signal_handlers
    check_dependencies
    setup_configuration
    setup_dependencies
    setup_nginx_proxy
    
    # 启动N.E.K.O服务
    if ! start_services; then
        echo "❌ Failed to start N.E.K.O services"
        exit 1
    fi
    
    # 启动Nginx代理
    if ! start_nginx_proxy; then
        echo "❌ Failed to start Nginx proxy"
        exit 1
    fi
    
    echo "🎉🎉 All systems operational!"
    echo "🌐 Web UI accessible via:"
    echo "   HTTP: http://localhost:${NGINX_PORT}"
    echo "   HTTPS: https://localhost:${NGINX_SSL_PORT}"
    echo "Use CTRL+C to stop all services"
    
    # 等待所有进程
    wait
}

# 执行主函数
main "$@"
