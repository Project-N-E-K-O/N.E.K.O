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
| `./neko-home` | `/home/neko` | 配置、角色、记忆、功能数据、TLS 证书与私钥、OpenFang 运行状态 |
| `./logs` | `/app/logs` | 日志 |

升级前备份数据，严禁公开数据或私钥目录。

::: danger 从旧版双挂载升级
旧版本分别挂载 `./N.E.K.O` 与 `./ssl`。不迁移就直接拉新镜像，容器会对着一个**空的**数据目录启动：服务照常运行、API Key 也会从环境变量重新生成，看上去没有异常，但人格、记忆、插件都不在。旧数据没有被删除，只是不再挂进容器。

顺序不能颠倒：`docker compose down` 会**删除**容器，而有些状态只存在于容器里。

```bash
# 1. 先导出只存在于容器内的东西，必须赶在删容器之前。
#    旧布局从没挂载 OpenFang 的工作目录；另外，若宿主机的 N.E.K.O/ 是空的，说明
#    此前跟的是旧版 README 的快速开始，其挂载目标（/root/Documents/N.E.K.O）与服务
#    实际写入的位置从来对不上，应用数据也在容器里。
#    末尾的 /. 表示复制目录内容，避免出现 N.E.K.O/N.E.K.O 这样多套一层。
mkdir -p neko-home/.local/share/N.E.K.O neko-home/ssl neko-home/.openfang
# 这里报 "No such container:path" 只说明从没初始化过 OpenFang，可以忽略；
# 其他错误（daemon 没起、权限、磁盘满）不能忽略，所以不加 `|| true` 吞掉
# 容器若已经把 neko-home 挂在 /home/neko 上，源和目标就是同一个 bind mount，
# 导出等于把目录复制到它自己；而那种情况下持有唯一副本的旧容器早已被重建掉了
if docker inspect neko --format '{{range .Mounts}}{{println .Destination}}{{end}}' 2>/dev/null | grep -qx /home/neko; then
  echo "容器已按新布局挂载，没有待导出的内容"
else
  docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/
  # 只有宿主目录为空时，应用数据才在容器里
  if [ -z "$(ls -A N.E.K.O 2>/dev/null)" ]; then
    docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/
  fi
fi
```

**确认第 1 步成功后再往下。** `docker compose down` 会删除容器，而在宿主 `N.E.K.O/` 为空的情况下，容器是那部分数据唯一的副本——导出若因权限、磁盘满或 daemon 没起而失败，请先解决再继续。

```bash
# 2. 停容器，再把宿主机上的旧目录按内容合并。若已经用新布局启动过一次，目标目录
#    已经存在（还带一张新生成的自签证书），直接 mv 会把旧目录套进去多一层。
#    同名文件以旧数据为准。
docker compose down
# 容器内的 neko 固定为 uid/gid 1000，和绝大多数发行版第一个普通用户一致，属主
# 通常已经对上。只有宿主账号不是 1000 时才需要这一步。
[ "$(id -u)" = 1000 ] || sudo chown -R "$(id -u):$(id -g)" neko-home
cp -a N.E.K.O/. neko-home/.local/share/N.E.K.O/ && rm -rf N.E.K.O
cp -a ssl/.     neko-home/ssl/                 && rm -rf ssl

# 3. 重新启动
docker compose up -d
```

`./logs` 不受影响。容器内的应用用户固定为 uid/gid **1000**（绝大多数 Linux 发行版第一个普通用户的号），因此 `neko-home/` 在宿主机上的属主就是你自己，备份和编辑都不需要 `sudo`。
:::

当前 Compose 没有 `build:`，旧的 `docker compose build` 说法无效。本地构建应在仓库根目录执行：

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

随后设置 `NEKO_IMAGE`。入口脚本生成的是自签名证书，不等于公网可信 TLS。诊断用 `docker compose ps`、`docker logs neko` 和 `curl -f http://127.0.0.1:48911/health`。
