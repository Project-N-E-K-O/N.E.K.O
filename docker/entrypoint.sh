#!/bin/bash
set -e

# N.E.K.O. Docker Entrypoint Script
PIDS=()

# 1. 信号处理优化
setup_signal_handlers() {
    trap 'echo "🛑 Received shutdown signal"; for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done; wait; exit 0' TERM INT
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
    
    echo "✅ Dependencies checked:"
    echo "   UV version: $(uv --version)"
    echo "   Python version: $(python3 --version)"
}

# 3. 配置管理优化
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
}

# 4. 数据持久化优化
setup_data_persistence() {
    echo "💾 Setting up data persistence..."
    local DATA_DIR="/data"
    
}

# 5. 依赖管理优化
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

# 6. 服务启动优化
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
        sleep 3  # 给服务启动留出时间
    done
    
    # 健康检查
    echo "🔍 Performing health checks..."
    sleep 10
    
    # 检查进程是否运行
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "✅ Process $pid is running"
        else
            echo "❌ Process $pid failed to start"
            return 1
        fi
    done
    
    # 检查主服务端口
    if command -v ss &> /dev/null; then
        if ss -tuln | grep -q ":${NEKO_MAIN_SERVER_PORT:-48911} "; then
            echo "✅ Main server is listening on port ${NEKO_MAIN_SERVER_PORT:-48911}"
        else
            echo "❌ Main server failed to bind to port"
            return 1
        fi
    else
        echo "⚠️ Port check skipped (ss command not available)"
    fi
    
    echo "🎉 All services started successfully!"
    echo "🌐 Web UI accessible at: http://localhost:${NEKO_MAIN_SERVER_PORT:-48911}"
    
    # 等待所有子进程
    wait
}

# 7. 主执行流程
main() {
    echo "=================================================="
    echo "   N.E.K.O. Container Startup - Robust Version"
    echo "=================================================="
    
    setup_signal_handlers
    check_dependencies
    setup_configuration
    setup_data_persistence
    setup_dependencies
    start_services
}

# 执行主函数
main "$@"
