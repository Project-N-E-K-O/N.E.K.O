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

## 📦 镜像版本选择

N.E.K.O. 镜像发布到两个 registry：

- **GHCR**：`ghcr.io/project-n-e-k-o/n.e.k.o:<tag>` — 所有 tag 都在（含主线 `ci-*` 滚动版）
- **Docker Hub**：`projectneko/n.e.k.o:<tag>` — **仅 release**（`latest`、`0.8.0-*` 等），避免主线 commit 污染对外 channel
- **镜像代理**（中国大陆建议优先）：`docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:<tag>`

### tag 流向一览

| tag | 何时更新 | 发到哪 | 适用场景 |
|---|---|---|---|
| `latest` / `latest-full` | 仅在 git tag `v*` push 时移动 | GHCR + Docker Hub | **默认推荐**，跟最新 release |
| `0.8.0-standard` / `0.8.0-full` | 该 release 打 tag 后定型 | GHCR + Docker Hub | 钉死某个 release，最稳 |
| `ci-standard` / `ci-full` | 每次 main commit 都会移动 | 仅 GHCR | 跟主线，**内测专用** |
| `ci-{commit}-standard` / `-full` | 该 main commit 打完后定型 | 仅 GHCR | 复现某个历史 main 版本 |
| `pr-{N}-ci-standard` / `-full` | （当前 PR 触发已关，仅在 workflow_dispatch 中可手动产） | 仅 GHCR | reviewer 拉下来手验产物 |
| `pr-ci-standard` / `-full` | 同上，且**任意** PR 触发都会被覆盖 | 仅 GHCR | 不要用，会被随便哪个 PR 顶掉 |

`standard`（~1.5GB）首次启动时下载 Chromium；`full`（~2.5GB）构建时已包含，开箱即用。

> 📦 **GHCR 自动清理**：[docker-cleanup.yml](../.github/workflows/docker-cleanup.yml) 每周清理一次，保留最近 30 个版本 + 上述浮动别名 + 所有 release 版本。`ci-{hash}-*` 和 `pr-{N}-ci-*` 这些一次性 tag 会被陆续回收。

### 推荐拉取方式

```bash
# 99% 的用户：跟最新 release（standard 版）
docker pull docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:latest

# 要预装 Chromium 的 full 版
docker pull docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:latest-full

# 钉死某个 release（生产环境推荐）
docker pull docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:0.8.0-standard

# 跟主线（开发者 / 内测）
docker pull docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:ci-standard
```

`docker-compose up` 默认就是 `latest`（即最新 release），不会被 main commit 或 PR 影响。要跟主线，在 `.env` 里设 `NEKO_IMAGE_VERSION=ci-standard` 或 `ci-full`。

> ⚠️ **警告**：`ci-*` 和 `pr-*` 是滚动 tag，每次合并 main / 推 PR 都会被覆盖。生产环境一律用 `latest` 或具体的 `{version}-*`。

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
- **运行时配置**: `utils/config_manager/core_config.py` 中的 `get_core_config()` 方法
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
- [Config Manager 源码](../utils/config_manager/)

