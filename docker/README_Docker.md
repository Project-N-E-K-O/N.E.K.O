# 🐳 Docker 部署指南

本文档说明如何将 N.E.K.O. 项目打包为 Docker 容器并部署。

### 远程首次连接与社区账户

远程网页/Windows Electron 首次输入实例 key，之后复用连接会话。默认允许明文 HTTP（`http://宿主地址:48911`、`DISABLE_SSL=1`、SSH 隧道），配对页会提示 key 与会话未加密；条件允许时优先 HTTPS（`https://宿主地址:48912`）。设置 `NEKO_REQUIRE_HTTPS=1` 可拒绝明文远程配对与凭证。浏览器只在 HTTPS 或 `localhost` 下允许麦克风，`http://IP` 访问时语音输入不可用。
管理员运行 docker compose exec --user neko -w /app neko-main uv run python -m utils.instance_access
配置HTTPS public origin即声明外层TLS网关：80端口关闭或仅HTTPS重定向，不可转发同Host明文请求，私有HTTP upstream不能暴露；Host相等不代表请求已加密。
取得持久化凭证；服务不在日志打印。多服务共享存储或设置同一 NEKO_INSTANCE_ACCESS_KEY。
外置 nginx/NAS 鉴权可叠加。匿名账户查询/API/WS 拒绝，授权后账户响应不含 Linux 路径或社区令牌。
默认 Web OAuth 使用平台固定 relay，无需每个 Docker 域名注册回调。认证平台与 PC 配套版本、
用户真实环境验收是 #3289 合并门槛；本机桌面与真实回环调试代理保持兼容。
详见[访问边界与测试步骤](/design/security/community-remote-access)。

### 外层反向代理与客户端地址

官方 nginx 的 HTTP/HTTPS 服务代理路由追加 `X-Forwarded-For` 转发链；main、memory、agent 和插件服务只信任回环代理 `127.0.0.1,::1`，Uvicorn 从右向左跳过可信代理，使用第一个不可信地址，避免客户端伪造最左回环地址。独立的 `/security/csrf-token` 引导路由仍覆盖 XFF。容器外再套 Traefik、Cloudflare 或 ingress 时，外层代理必须正确设置客户端地址链；非回环外层上游若需进一步信任，应由运维明确配置具体可信代理地址或 nginx `set_real_ip_from` / `real_ip_header`，不要使用信任所有地址的通配符。仅限本机的资源接口及插件 UI push 拒绝转发调用；后端进程可不带转发元数据直连本机接口。

主服务所有启动入口开启 Uvicorn 代理头解析，并显式仅信任 `127.0.0.1,::1`，不受 `FORWARDED_ALLOW_IPS=*` 覆盖。桌面本机调试代理报告 XFF 为回环地址时保持可用；同机 HTTP 隧道报告远程地址时，本机账户/资源守卫拒绝。`NEKO_BEHIND_PROXY=1/true/yes` 还会启用更严格的本机资源转发元数据检查；插件原生变更、开发接口、UI push 和 bridge-token 始终拒绝转发元数据。外置代理必须保留真实客户端 XFF 链，不能覆盖成回环地址；不含 XFF、只带 X-Real-IP/Forwarded 的代理不被 Uvicorn 解析为客户端身份，纯 TCP 隧道也无法靠 HTTP 元数据识别，因此远程部署仍须声明并提供实例访问认证。

## 📋 目录结构

```
docker/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 配置
├── .env.example           # 环境变量模板
├── watchdog/              # 可选宿主机自愈看门狗（见下文「低配云服务器」）
└── config/                # 配置示例（运行时不挂载此目录）
    ├── core_config.json.example
    ├── characters.json.example
    └── api_providers.json
```

## 🔧 配置项说明

### 方式一：环境变量配置（推荐）

环境变量用于首次启动时生成持久化初始配置。之后以 `/home/neko/.local/share/N.E.K.O/config` 中的运行时配置为准；只有显式设置 `NEKO_FORCE_ENV_UPDATE` 才会用环境变量重新生成并覆盖该初始配置。

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

### 方式二：配置文件（高级用户）

运行时配置位于容器的 `/home/neko/.local/share/N.E.K.O/config`。挂载 `./neko-home:/home/neko` 即可持久化；不要再挂载镜像内的 `/app/config`。

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
  -p 48911:80 \
  -p 48912:443 \
  -e TZ="Asia/Shanghai" \
  -e NEKO_CORE_API_KEY="your-api-key" \
  -e NEKO_CORE_API="qwen" \
  -e XDG_DATA_HOME="/home/neko/.local/share" \
  -e NEKO_STORAGE_SELECTED_ROOT="/home/neko/.local/share/N.E.K.O" \
  -e NEKO_STORAGE_ANCHOR_ROOT="/home/neko/.local/share/N.E.K.O" \
  -v $(pwd)/neko-home:/home/neko \
  -v $(pwd)/logs:/app/logs \
  neko:latest
```

## 📂 数据持久化

建议将完整的用户主目录挂载到宿主机：

- `/home/neko` - 配置、记忆、角色、用户插件及插件数据、插件市场 OAuth 登录状态和 TLS 证书/私钥
- `/app/logs` - 日志

示例：

```yaml
volumes:
  - ./neko-home:/home/neko
  - ./logs:/app/logs
```

## 🔍 配置优先级

配置加载优先级（从高到低）：

1. **持久化运行时配置** - `/home/neko/.local/share/N.E.K.O/config/*.json`
2. **初始化输入** - 首次启动时由 Compose / `docker run` 传入的 `NEKO_*` 环境变量生成；设置 `NEKO_FORCE_ENV_UPDATE` 会显式重新生成该配置
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

## 🐛 故障排查

### 检查配置加载

```bash
# 进入容器
docker exec -it neko bash

# 检查生效的持久化配置文件
cat /home/neko/.local/share/N.E.K.O/config/core_config.json

# 检查环境变量
env | grep NEKO_

# 查看日志
tail -f /app/logs/*.log
```

### 常见问题

**Q: 环境变量不生效？**
A: 环境变量仅用于首次生成初始配置。已有持久化配置时，请在 Web UI 修改配置；不要期待重新启动后由环境变量覆盖。

**Q: 配置文件被覆盖？**
A: 正常重启不会覆盖已有持久化配置。`NEKO_FORCE_ENV_UPDATE` 会用当前环境变量重新生成并覆盖持久化的 `core_config.json`；使用前请先备份，日常修改请通过 Web UI 或明确编辑持久化配置。

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

## 🪶 低配云服务器

2 核 2G 等低配服务器同样使用本目录的 `docker-compose.yml`。宿主机内存（ZRAM/Swap）、磁盘、安全配置，以及可选的宿主机自愈看门狗（`docker/watchdog/`）见[低配云服务器部署](../docs/zh-CN/deployment/low-spec-server.md)。

## 📚 更多资源

- [项目 README](../README.MD)
- [配置系统说明](../config/__init__.py)
- [Config Manager 源码](../utils/config_manager/)
