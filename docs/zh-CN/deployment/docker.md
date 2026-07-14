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
    # Image version is selectable via env vars (latest = newest release)
    image: ${NEKO_IMAGE:-docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:${NEKO_IMAGE_VERSION:-latest}}
    container_name: neko
    restart: unless-stopped
    ports:
      - "48911:80"    # HTTP
      - "48912:443"   # HTTPS
    volumes:
      - ./N.E.K.O:/home/neko/.local/share/N.E.K.O
      - ./logs:/app/logs
      - ./ssl:/home/neko/ssl
    networks:
      - neko-network

networks:
  neko-network:
    driver: bridge
```

配置通过上面创建的 `.env` 文件提供（`cp env.template .env`）；`entrypoint.sh` 在启动时读取其中的 `NEKO_*` 变量。完整列表请参阅[环境变量](#环境变量)。

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
| `./N.E.K.O` | `/home/neko/.local/share/N.E.K.O` | 配置、角色、记忆 |
| `./logs` | `/app/logs` | 应用日志 |
| `./ssl` | `/home/neko/ssl` | SSL 证书 |

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
docker exec neko cat /home/neko/.local/share/N.E.K.O/core_config.json

# Check environment
docker exec neko env | grep NEKO_
```
