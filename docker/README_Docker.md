# 🐳 Docker 部署指南

本文档说明如何将 N.E.K.O. 项目打包为 Docker 容器并部署。

## 📋 目录结构

```
docker/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 配置
├── .env.example           # 环境变量模板
└── config/                # 配置文件目录（挂载用）
    ├── core_config.json.example
    ├── characters.json.example
    └── api_providers.json
```

## 🔧 配置项说明

### 方式一：环境变量配置（推荐）

所有配置都可以通过环境变量设置。环境变量会覆盖配置文件中的值。

#### 核心 API 配置

| 环境变量 | 说明 | 默认值 | 示例 |
|---------|------|--------|------|
| `NEKO_CORE_API_KEY` | 核心 API Key（必填） | - | `sk-xxxxx` |
| `NEKO_CORE_API` | 核心 API 提供商 | `qwen` | `qwen`, `openai`, `glm`, `step`, `free` |
| `NEKO_ASSIST_API` | 辅助 API 提供商 | `qwen` | `qwen`, `openai`, `glm`, `step`, `silicon` |
| `NEKO_ASSIST_API_KEY_QWEN` | 阿里云 API Key | - | `sk-xxxxx` |
| `NEKO_ASSIST_API_KEY_OPENAI` | OpenAI API Key | - | `sk-xxxxx` |
| `NEKO_ASSIST_API_KEY_GLM` | 智谱 API Key | - | `xxxxx` |
| `NEKO_ASSIST_API_KEY_STEP` | 阶跃星辰 API Key | - | `xxxxx` |
| `NEKO_ASSIST_API_KEY_SILICON` | 硅基流动 API Key | - | `xxxxx` |
| `NEKO_MCP_TOKEN` | MCP Router Token | - | `xxxxx` |

#### 服务器端口配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `NEKO_MAIN_SERVER_PORT` | 主服务器端口 | `48911` |
| `NEKO_MEMORY_SERVER_PORT` | 记忆服务器端口 | `48912` |
| `NEKO_MONITOR_SERVER_PORT` | 监控服务器端口 | `48913` |
| `NEKO_TOOL_SERVER_PORT` | 工具服务器端口 | `48915` |

#### 模型配置（高级）

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `NEKO_SUMMARY_MODEL` | 摘要模型 | `qwen-plus` |
| `NEKO_CORRECTION_MODEL` | 纠错模型 | `qwen-max` |
| `NEKO_EMOTION_MODEL` | 情感分析模型 | `qwen-turbo` |
| `NEKO_VISION_MODEL` | 视觉模型 | `qwen3-vl-plus-2025-09-23` |

### 方式二：配置文件（高级用户）

挂载配置文件到容器的 `/app/config` 目录。

#### core_config.json

```json
{
  "coreApiKey": "your-api-key-here",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "assistApiKeyQwen": "",
  "assistApiKeyOpenai": "",
  "assistApiKeyGlm": "",
  "assistApiKeyStep": "",
  "assistApiKeySilicon": "",
  "mcpToken": ""
}
```

#### characters.json

```json
{
  "主人": {
    "档案名": "主人",
    "性别": "男",
    "昵称": "主人"
  },
  "猫娘": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "小天",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  },
  "当前猫娘": "小天"
}
```

## 🚀 快速开始

### 1. 使用 docker-compose（推荐）

```bash
# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env 文件，填入你的 API Key
nano .env

# 3. 启动服务
docker-compose up -d

# 4. 查看日志
docker-compose logs -f

# 5. 停止服务
docker-compose down
```

### 2. 使用 docker run

```bash
docker run -d \
  --name neko \
  -p 48911:48911 \
  -e NEKO_CORE_API_KEY="your-api-key" \
  -e NEKO_CORE_API="qwen" \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/memory:/app/memory \
  -v $(pwd)/static:/app/static \
  neko:latest
```

## 📂 数据持久化

建议挂载以下目录到宿主机：

- `/app/config` - 配置文件目录
- `/app/memory` - 记忆数据目录
- `/app/static` - Live2D 模型和静态资源
- `/app/logs` - 日志文件目录

示例：

```yaml
volumes:
  - ./config:/app/config
  - ./memory:/app/memory
  - ./static:/app/static
  - ./logs:/app/logs
```

## 🔍 配置优先级

配置加载优先级（从高到低）：

1. **环境变量** - `NEKO_*` 开头的环境变量
2. **挂载的配置文件** - `/app/config/*.json`
3. **内置默认值** - 代码中定义的默认值

## 📝 完整配置参考

查看所有可配置项，请参考：

- **基础配置**: `config/__init__.py` 中的 `DEFAULT_CORE_CONFIG`
- **运行时配置**: `utils/config_manager.py` 中的 `get_core_config()` 方法
- **API 提供商配置**: `config/api_providers.json`

### 所有可配置的环境变量

#### API Keys 和认证
```bash
NEKO_CORE_API_KEY=          # 核心 API Key
NEKO_ASSIST_API_KEY_QWEN=   # 阿里云 API Key
NEKO_ASSIST_API_KEY_OPENAI= # OpenAI API Key
NEKO_ASSIST_API_KEY_GLM=    # 智谱 API Key
NEKO_ASSIST_API_KEY_STEP=   # 阶跃星辰 API Key
NEKO_ASSIST_API_KEY_SILICON=# 硅基流动 API Key
NEKO_MCP_TOKEN=             # MCP Router Token
```

#### API 提供商选择
```bash
NEKO_CORE_API=qwen          # 核心 API: qwen|openai|glm|step|free
NEKO_ASSIST_API=qwen        # 辅助 API: qwen|openai|glm|step|silicon
```

#### 服务器端口
```bash
NEKO_MAIN_SERVER_PORT=48911
NEKO_MEMORY_SERVER_PORT=48912
NEKO_MONITOR_SERVER_PORT=48913
NEKO_TOOL_SERVER_PORT=48915
```

#### 模型选择
```bash
NEKO_SUMMARY_MODEL=qwen-plus
NEKO_CORRECTION_MODEL=qwen-max
NEKO_EMOTION_MODEL=qwen-turbo
NEKO_VISION_MODEL=qwen3-vl-plus-2025-09-23
```

#### MCP Router
```bash
NEKO_MCP_ROUTER_URL=http://localhost:3283
```

## 🐛 故障排查

### 检查配置加载

```bash
# 进入容器
docker exec -it neko bash

# 检查配置文件
cat /app/config/core_config.json

# 检查环境变量
env | grep NEKO_

# 查看日志
tail -f /app/logs/*.log
```

### 常见问题

**Q: 环境变量不生效？**
A: 确保环境变量名以 `NEKO_` 开头，并且已在启动时传入。

**Q: 配置文件被覆盖？**
A: 环境变量优先级高于配置文件。如果想使用配置文件，不要设置对应的环境变量。

**Q: 如何查看所有配置项？**
A: 运行 `docker exec neko python -c "from utils.config_manager import get_config_manager; import json; print(json.dumps(get_config_manager().get_core_config(), indent=2, ensure_ascii=False))"`

## 🔐 安全建议

1. **不要将 API Key 提交到 Git**
   - 使用 `.env` 文件（已在 `.gitignore` 中）
   - 或使用 Docker secrets

2. **使用 Docker secrets（生产环境）**
   ```yaml
   secrets:
     neko_api_key:
       external: true
   services:
     neko:
       secrets:
         - neko_api_key
   ```

3. **限制容器权限**
   ```yaml
   security_opt:
     - no-new-privileges:true
   read_only: true
   ```

## 📚 更多资源

- [项目 README](../README.MD)
- [配置系统说明](../config/__init__.py)
- [Config Manager 源码](../utils/config_manager.py)

