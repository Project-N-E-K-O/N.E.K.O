# Docker 部署

维护中的 Compose 是 `docker/docker-compose.yml`。Nginx 前置，宿主 48911 为 HTTP、48912 为 HTTPS。

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O/docker
cp env.template .env
# 审核 .env，只保留当前代码支持的值
docker compose up -d
```

打开 `http://127.0.0.1:48911`。需要可复现时固定 `NEKO_IMAGE` 或 `NEKO_IMAGE_VERSION`。`latest` 为 standard 别名，`latest-full` 为 full。

入口脚本只在 `/app/config/core_config.json` 不存在或设置 `NEKO_FORCE_ENV_UPDATE` 时生成初始配置。API 环境变量不是实时通用覆盖，启动后请在 Web UI 确认。

| 宿主 | 容器 | 用途 |
| --- | --- | --- |
| `./N.E.K.O` | `/home/neko/.local/share/N.E.K.O` | 配置、角色、记忆、用户插件、插件数据和常规日志 |
| `./openfang` | `/home/neko/.openfang` | OpenFang 配置和运行状态 |
| `./neko-home` | `/home/neko/.neko` | 插件市场 OAuth 登录状态等用户级凭据 |
| `./logs` | `/app/logs` | 源码模式调试日志回退；常规日志见 `./N.E.K.O/logs` |
| `./ssl` | `/home/neko/ssl` | TLS 证书/私钥 |

`TZ` 默认是 `Asia/Shanghai`，可在 `.env` 改为任意 IANA 时区（例如 `Etc/UTC`）。升级前备份 `N.E.K.O`、`openfang`、`neko-home` 和 `ssl`；严禁公开数据或私钥目录。不要用 `PLUGIN_CONFIG_ROOT`、`PLUGIN_PACKAGES_ROOT` 或 `PACKAGE_PROFILES_ROOT` 指向这些挂载之外的路径，否则对应用户插件数据不会随容器持久化。

当前 Compose 没有 `build:`，旧的 `docker compose build` 说法无效。本地构建应在仓库根目录执行：

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

随后设置 `NEKO_IMAGE`。入口脚本生成的是自签名证书，不等于公网可信 TLS。诊断用 `docker compose ps`、`docker logs neko` 和 `curl -f http://127.0.0.1:48911/health`。
