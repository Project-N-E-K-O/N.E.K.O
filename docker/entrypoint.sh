#!/bin/bash
set -e

# N.E.K.O. Docker Entrypoint Script
# 将环境变量转换为配置文件

echo "🐱 Starting N.E.K.O. Docker Container..."

# 配置文件路径
CONFIG_DIR="/app/config"
CORE_CONFIG_FILE="$CONFIG_DIR/core_config.json"

# 确保配置目录存在
mkdir -p "$CONFIG_DIR"

# 如果配置文件不存在或需要从环境变量更新
if [ ! -f "$CORE_CONFIG_FILE" ] || [ ! -z "$NEKO_FORCE_ENV_UPDATE" ]; then
    echo "📝 Generating configuration from environment variables..."
    
    # 从环境变量生成 core_config.json
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
    
    echo "✅ Configuration file created: $CORE_CONFIG_FILE"
else
    echo "📄 Using existing configuration file: $CORE_CONFIG_FILE"
fi

# 显示配置信息（隐藏敏感信息）
echo "🔧 Configuration:"
echo "  Core API Provider: ${NEKO_CORE_API:-qwen}"
echo "  Assist API Provider: ${NEKO_ASSIST_API:-qwen}"
echo "  Main Server Port: ${NEKO_MAIN_SERVER_PORT:-48911}"
echo "  Memory Server Port: ${NEKO_MEMORY_SERVER_PORT:-48912}"

# 检查必要的 API Key
if [ -z "$NEKO_CORE_API_KEY" ] && [ "${NEKO_CORE_API}" != "free" ]; then
    echo "⚠️  Warning: NEKO_CORE_API_KEY is not set!"
    echo "   Set it via environment variable or mount a config file."
fi

# 执行传入的命令
echo "🚀 Starting service: $@"
exec "$@"

