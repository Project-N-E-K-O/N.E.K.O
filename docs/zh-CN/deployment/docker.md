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
| `./N.E.K.O` | `/home/neko/.local/share/N.E.K.O` | 配置、角色、记忆、功能数据 |
| `./logs` | `/app/logs` | 日志 |
| `./ssl` | `/home/neko/ssl` | TLS 证书/私钥 |

升级前备份数据，严禁公开数据或私钥目录。

当前 Compose 没有 `build:`，旧的 `docker compose build` 说法无效。本地构建应在仓库根目录执行：

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

随后设置 `NEKO_IMAGE`。入口脚本生成的是自签名证书，不等于公网可信 TLS。诊断用 `docker compose ps`、`docker logs neko` 和 `curl -f http://127.0.0.1:48911/health`。
