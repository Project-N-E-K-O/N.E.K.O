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
├── Dockerfile              # Docker 镜像构建文件（标准版）
├── Dockerfile.full         # 完整版镜像（内置 Chromium）
├── docker-compose.yml      # Docker Compose 配置
├── entrypoint.sh           # 容器入口：证书、配置、服务与 Nginx 编排
├── env.template            # 环境变量模板（复制为 docker/.env）
├── preflight.sh            # 可选宿主侧预检：挂载源符号链接与属主
├── backup.sh               # 可选宿主侧备份：主副本清单 + 校验和（见「备份与恢复」）
├── compose.limits.example.yaml  # 可选的资源上限覆盖示例（数值必须实测）
├── nginx/                  # 反向代理配置分层模板（见「反向代理配置」一节）
├── watchdog/               # 可选宿主机自愈看门狗（见下文「低配云服务器」）
└── config/                # 配置示例（运行时不挂载此目录）
    ├── core_config.json.example
    ├── characters.json.example
    └── api_providers.json
```

## 🌐 反向代理配置

容器内的 Nginx 反代配置由 `docker/nginx/` 下的分层模板在启动时渲染，分成四层：

| 层 | 文件 | 负责 |
|---|---|---|
| L0 | `nginx.conf.template` | 进程与 `http` 全局 |
| L1a | `server.http.conf.template` / `server.tls.conf.template` | 监听与 TLS —— **HTTP / HTTPS 唯一的差异** |
| L1b | `neko-proxy.conf.template` | `server` 骨架，组装 L1a 与 L1c |
| L1c | `locations.conf.template` | 路由表，两种模式共用一份 |

**改路由只需要改一个文件**：`locations.conf.template`。它不是按部署模式复制的两份配置，
因此不存在「只改了 HTTP 那份」的静默失效。改完跑一次分层回归测试：

```bash
bash docker/nginx/test-render.sh
```

分层依据、不变量与新增变量/路由的步骤见 [`docker/nginx/README.md`](nginx/README.md)。

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

### 1. 使用 Docker Compose（推荐）

> **前置条件：Docker Compose v2（≥ 2.24）**，即 `docker compose`（空格）而不是已停止维护的
> `docker-compose`（v1）。本文件用了 `env_file` 的 `required: false` 形式，v1 和 2.24 之前的 v2
> 会直接拒绝解析整份配置。
> 只能装旧版时，把 compose 里的 `env_file:` 段改成 `env_file: [.env]` 并确保 `.env` 存在。

```bash
# 1. 复制环境变量模板（仓库里的文件名是 env.template）
cp env.template .env

# 2. 编辑 .env 文件，填入你的 API Key
nano .env

# 3. 启动服务（在 docker/ 目录下执行）
docker compose up -d

# 4. 查看日志
docker compose logs -f

# 5. 停止服务
docker compose down
```

#### 环境变量是怎么进容器的

`docker/docker-compose.yml` 用 `env_file: .env` 把 `.env` 注入容器，所以复制完
`env.template` 就能照常填 Key。

这一点值得单独说明，因为 **Compose 默认只把 `.env` 用于 `${}` 插值**：没有被
`environment` 或 `env_file` 引用的变量不会进容器。API Key 这类变量此前就属于
"填了但没生效"，而症状是**启动一切正常、Web UI 里却显示未配置**。

`.env` 缺失时部署照常启动（`required: false`），此时容器内没有 Key，需要启动后
在 Web UI 里配置。

#### 多实例与端口覆盖

同宿主机跑多个实例时，改这几个变量即可（默认值即单实例行为）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEKO_CONTAINER_NAME` | `neko` | 容器名 |
| `NEKO_HOME_DIR` | `./neko-home` | 数据目录 |
| `NEKO_LOGS_DIR` | `./logs` | 日志目录 |
| `NEKO_HTTP_PORT` / `NEKO_HTTPS_PORT` | `48911` / `48912` | 宿主端口 |
| `NEKO_MEMORY_SERVER_PORT` 等三个 | `48912`/`48915`/`48916` | **容器内**服务端口 |

容器内端口这三个会被入口脚本渲染进 Nginx 路由表，所以"服务绑哪里"和"反代转发到
哪里"始终一致 —— 不会出现改了端口却 502 的情况。

```bash
NEKO_CONTAINER_NAME=neko-2 NEKO_HOME_DIR=./neko-home-2 NEKO_LOGS_DIR=./logs-2 \
NEKO_HTTP_PORT=48921 NEKO_HTTPS_PORT=48922 \
docker compose -p neko-2 up -d
```

`NEKO_LOGS_DIR` 也不能省：两个实例若共用 `./logs`，在源码模式 DEBUG 日志或
后备日志开启时，两边会往同一批按「服务/日期」命名的文件里追加，并互相抢轮转 ——
日志会交错、甚至被对方轮转掉。

`-p neko-2` 不能省：只改容器名/端口而沿用同一个 Compose 项目名，Compose 仍认为是同一项目的
同一个 `neko-main` 服务，会去**更新第一个实例**而不是起第二个。

装看门狗时每个实例要在 cron 行上给出容器名与宿主端口，见 [`watchdog/README.md`](watchdog/README.md)。

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

首次启动前、以及迁入数据之后，建议在 `docker/` 下执行一次宿主机预检（不拉取任何镜像）：

```bash
sudo sh preflight.sh                      # 默认检查 ./neko-home 和 ./logs
sudo sh preflight.sh /覆盖文件里的/neko-home /覆盖文件里的/logs   # 覆盖文件挂载了其他目录时原样传入，相对路径按 docker/ 解析
```

它拒绝本身是、或路径中经过符号链接的挂载来源（Docker 会挂载链接目标，容器会接管它的属主），创建缺失的目录，并只把这两个目录本身的属主设为 uid/gid 1000。数据目录内部的属主由入口脚本每次启动时对齐。回归测试：`sudo bash test-preflight.sh`。

### 插件数据存在哪

插件相关的四类数据**默认全部在 `neko-home` 内**，因此容器重建不会丢：

| 内容 | 默认位置（宿主侧） |
| --- | --- |
| 插件状态（配置、数据） | `neko-home/.local/share/N.E.K.O/plugins/` |
| 用户安装的插件代码（含 `vendor/` 依赖） | `neko-home/.local/share/N.E.K.O/.neko-plugin-installations/plugins/` |
| 安装包落地（`.neko-plugin` / `.neko-bundle`） | `neko-home/.local/share/N.E.K.O/.neko-plugin-packages/` |
| 包 profile | `neko-home/.local/share/N.E.K.O/.neko-package-profiles/` |
| **内置插件**（随镜像发布） | 镜像内的 `/app/plugin/plugins/`，随镜像升级 |

两个坑：

- `PLUGIN_CONFIG_ROOT` / `PLUGIN_PACKAGES_ROOT` / `PACKAGE_PROFILES_ROOT` 这三个变量一旦指向
  `/home/neko` 之外，对应数据就**不会随容器保存**，而且没有任何报错。入口脚本现在会在启动时
  检测并打印告警；确实要自定义目录时，记得把那个目录也加进 compose 的 `volumes`。
- **不要两个实例共用同一个 `neko-home`**。用多实例参数（`NEKO_CONTAINER_NAME` / `NEKO_HOME_DIR`）跑多个
  实例时，每个实例必须有自己独立的数据目录，否则插件状态与记忆会互相覆盖。

插件作者侧的开发流程（本仓库内编辑插件、构建、发布）见 `plugin/` 下的 CLI 文档；容器内已经带 `uv`，
插件的 Python 依赖走 `uv pip install --target vendor/`，落在插件目录内，同样随 `neko-home` 持久化。

### 备份与恢复

`docker/backup.sh` 在**宿主机**运行，只做三件客观的事：按固定清单打包**不可重建的主副本**、
排除可以从镜像 / 网络重建的内容、生成校验和与清单让恢复方能验证。频率与保留份数由你决定。

```bash
sudo sh backup.sh                        # 冷备到 docker/backups/（默认先停容器）
sudo sh backup.sh --keep 7               # 只保留最近 7 份
sudo sh backup.sh --dry-run              # 只看会打包/排除什么，不写文件
sudo sh backup.sh --no-stop              # 不停容器（放弃一致性保证，会明确警告）
sudo sh backup.sh --slim                 # 瘦身：排除插件 vendor/（见下）
sudo sh backup.sh --container neko-2 --home ./neko-home-2    # 多实例
```

**插件 `vendor/` 默认会被备份。** 那是插件的 Python 依赖，容器启动时不会重建 ——
少了它，恢复出来的插件会报依赖缺失并被跳过：**数据回来了，能力没回来**。
确实要控制体积时用 `--slim`，代价是恢复后需要重新安装这些插件的依赖。

脚本要求 **Linux 宿主**（用到 GNU `stat`/`tar`/`flock`）；BSD/macOS 上会先明确报错，
不会静默失败。同一实例的备份会有实例级锁：即使两次调用用了不同的 `--out`，也不会重叠。

**默认停机是刻意的**：`memory/` 下有 SQLite（`time_indexed.db`），容器运行中直接打包可能拷到
**撕裂的数据库**，而症状要到恢复当天才出现。不能停机就用 `--no-stop`，脚本会明确告诉你这次没有保证。

**无法确认容器状态时脚本会拒绝执行**（docker CLI 缺失、容器名写错、daemon 不可用）：那不是
"容器没跑"的证据，可能正对着一个仍在写入的数据目录。此时要么修好 `--container`，要么显式
用 `--no-stop` 接受风险 —— 不会默默按冷备继续。

备份产物默认只有本人可读（`umask 077`：目录 700、归档 600）：归档里有 API Key 与 TLS 私钥。

产出三件：`neko-backup-<UTC>.tar.gz`、`.sha256`（`sha256sum -c` 可验证）、`.manifest.txt`（文件清单）。

**恢复**（脚本每次都会把这几步印在输出结尾）：

> 多实例恢复：第 1、4 步必须**复用该实例启动时的 `-p` 与同一组 `NEKO_*` 覆盖值**
> （尤其是 `NEKO_HOME_DIR` 与端口）。否则 Compose 会去操作默认项目，目标容器可能仍在
> 往你要解压的目录里写 —— SQLite 会和解压出来的文件打架，`up` 起的也不是那个实例。

1. `docker compose down`
2. 先把现有 `neko-home` **改名留档** —— 万一恢复的是错的
3. `tar -xzf <归档> -C <neko-home>`（归档内是 `.local/` 与 `ssl/`，所以解到数据目录本身，不是它的父目录）
4. `docker compose up -d`
5. 在 Web UI 确认角色 / 记忆 / 插件都在；实例访问密钥若一并恢复，终端**不需要重新配对**

> 备份包含 `config/core_config.json`（含 API Key）与 `instance_access.key`，
> **按机密对待**：不要贴到 issue、聊天或公开的存储。

回归测试：`sh test-backup.sh`（只碰临时目录，不停任何容器）。

### 健康检查与自愈

Compose 自带 `healthcheck`，**三个后端服务任何一个挂掉都会判为 unhealthy**：

```bash
# 多实例时把容器名换成该实例的 NEKO_CONTAINER_NAME
docker inspect --format '{{.State.Health.Status}}' "${NEKO_CONTAINER_NAME:-neko}"
```

它走的是**容器内回环直探后端端口**（默认 `127.0.0.1:48911/48912/48915`）。多实例时探的是
**该容器自己的** `NEKO_*_SERVER_PORT` —— 端口非法会让容器拒绝启动，不会出现"服务在一个端口、
探针在另一个端口"。

之所以必须走容器内回环：从宿主打
`/health` 会撞实例访问门禁拿 401，探针会永远"不健康"，编排层就会反复重启一个本来正常的容器。

**但 unhealthy 不会触发重启** —— Docker 的 `restart` 策略只对进程退出生效。healthcheck 提供的是
**可见性**（`docker ps`、外部监控、将来 sidecar 的 `depends_on: service_healthy`）。真要"卡死自动救活"，
请装可选宿主看门狗 `docker/watchdog/`，两者互补。

启动期较长（三个服务各 5s + 15s 健康检查 + nginx 3s，standard 镜像首次还会下载 Chromium），
所以 `start_period` 放宽到 5 分钟；这段时间显示 `starting` 是正常的。

### 内存与 CPU 上限

**Compose 默认不设任何上限，Docker 也就不做任何保护**：宿主机 OOM killer 按自己的评分挑进程杀，
通常挑内存占用最大的那个 —— 你的主服务或插件进程。症状是"某个功能突然不好使"，与内存看起来毫无关系。

这里**刻意不给默认值**：给多少取决于你的负载，套一个经验值比不设更危险（设小了会在峰值 OOM）。
先测，再填。

```bash
# 1) 当前有没有上限（0 = 没设，cpus=0 同理）
docker inspect neko --format 'memory={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} cpus={{.HostConfig.NanoCpus}}'

# 2) 容器内各服务实际占用（RSS，KB），按占用排序
docker exec neko sh -c 'for p in /proc/[0-9]*; do
    c=$(tr "\0" " " < "$p/cmdline" 2>/dev/null | cut -c1-60)
    r=$(awk "/VmRSS/{print \$2}" "$p/status" 2>/dev/null)
    [ -n "$r" ] && echo "$r KB  $c"
  done | sort -rn | head'

# 3) 宿主可用内存与 swap / zram
free -h
```

在**代表性负载**下测峰值（多终端同时连、语音开着、插件跑着），然后把数值填进
`docker/compose.limits.example.yaml` 并这样启动：

```bash
docker compose -f docker-compose.yml -f compose.limits.example.yaml up -d
```

低配机器的内存/swap/zram 整备见 [低配云服务器](https://project-neko.online/deployment/low-spec-server)。

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
