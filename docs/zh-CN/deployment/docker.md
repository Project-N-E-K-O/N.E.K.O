# Docker 部署

## 快速开始

```bash
# Clone the repository
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O/docker

# Configure environment
cp env.template .env
# Edit .env with your API keys

# Start
docker-compose up -d
```

在 `http://localhost:48911` 访问 Web UI。

## docker-compose.yml

```yaml
version: '3.8'

services:
  neko-main:
    image: ghcr.io/project-n-e-k-o/n.e.k.o:latest
    container_name: neko
    restart: unless-stopped
    ports:
      - "48911:80"
    environment:
      - NEKO_CORE_API_KEY=${NEKO_CORE_API_KEY}
      - NEKO_CORE_API=${NEKO_CORE_API:-qwen}
      - NEKO_ASSIST_API=${NEKO_ASSIST_API:-qwen}
      - NEKO_ASSIST_API_KEY_QWEN=${NEKO_ASSIST_API_KEY_QWEN:-}
      - NEKO_ASSIST_API_KEY_OPENAI=${NEKO_ASSIST_API_KEY_OPENAI:-}
      - NEKO_ASSIST_API_KEY_GLM=${NEKO_ASSIST_API_KEY_GLM:-}
      - NEKO_ASSIST_API_KEY_STEP=${NEKO_ASSIST_API_KEY_STEP:-}
      - NEKO_ASSIST_API_KEY_SILICON=${NEKO_ASSIST_API_KEY_SILICON:-}
      - NEKO_MCP_TOKEN=${NEKO_MCP_TOKEN:-}
    volumes:
      - ./N.E.K.O:/root/Documents/N.E.K.O
      - ./logs:/app/logs
    networks:
      - neko-network

networks:
  neko-network:
    driver: bridge
```

## 环境变量

从模板创建 `.env` 文件：

```bash
# Required
NEKO_CORE_API_KEY=sk-your-key-here
NEKO_CORE_API=qwen

# Optional
NEKO_ASSIST_API=qwen
NEKO_ASSIST_API_KEY_QWEN=sk-your-assist-key
```

完整参考请参阅[环境变量](/config/environment-vars)。

## Nginx 代理

Docker 容器内置 Nginx 作为反向代理：

- 代理到内部端口上的主服务器
- 支持 WebSocket 以实现实时聊天
- 静态文件缓存（30 天过期）
- 健康检查端点 `/health`

## 数据持久化

| 挂载 | 容器路径 | 用途 |
|------|----------|------|
| `./N.E.K.O` | `/root/Documents/N.E.K.O` | 配置、角色、记忆 |
| `./logs` | `/app/logs` | 应用日志 |

## 服务商快速配置

**Qwen（推荐）：**
```bash
NEKO_CORE_API_KEY=sk-xxxxx
NEKO_CORE_API=qwen
```

**免费（无需密钥）：**
```bash
NEKO_CORE_API_KEY=free-access
NEKO_CORE_API=free
```

**OpenAI：**
```bash
NEKO_CORE_API_KEY=sk-xxxxx
NEKO_CORE_API=openai
```

## 故障排查

```bash
# View logs
docker logs neko

# Enter container
docker exec -it neko bash

# Check config
docker exec neko cat /root/Documents/N.E.K.O/core_config.json

# Check environment
docker exec neko env | grep NEKO_
```
