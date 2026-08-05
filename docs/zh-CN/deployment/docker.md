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
# 判据用「容器实际挂了什么」，而不是「宿主目录里有没有东西」：旧版 README 把
# ./N.E.K.O 挂到了 /root/Documents/N.E.K.O，那是服务从不写入的路径，所以那个宿主
# 目录里可能有你自己放的文件，而真数据仍然只在容器可写层里。
MOUNTS=$(docker inspect neko --format '{{range .Mounts}}{{println .Destination}}{{end}}' 2>/dev/null)

# 第 4 步要用：真数据是从容器里捞出来的，就不能再被宿主机上那个旧目录覆盖
EXPORTED_APP_DATA=""

if [ -z "$MOUNTS" ]; then
  echo "容器 neko 不在（可能已经删过），跳过导出"
elif printf '%s\n' "$MOUNTS" | grep -qx /home/neko; then
  echo "容器已按新布局挂载，没有待导出的内容"
else
  # 应用数据先导，这部分丢了找不回来。容器没把数据目录挂出去，就说明它只存在于
  # 容器可写层。
  if ! printf '%s\n' "$MOUNTS" | grep -qx /home/neko/.local/share/N.E.K.O; then
    docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/
    EXPORTED_APP_DATA=1
  fi
  # OpenFang 状态其次，且不致命：从没初始化过的话容器里就没这个目录，而 docker cp
  # 对不存在的 SRC_PATH 是报错退出的 —— 不能让它挡住上面已经完成的关键导出。
  docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/ \
    || echo "（容器里没有 .openfang，或导出失败；不影响上面的应用数据）"
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
# 宿主机上那份只有在「第 1 步没从容器里救数据」时才是权威：旧版 README 把该目录
# 挂到了服务从不写入的路径，里面的东西会覆盖掉唯一正确的那份。
if [ -n "$EXPORTED_APP_DATA" ]; then
  echo "应用数据已在第 1 步从容器导出，不合并宿主机上的 N.E.K.O/"
elif [ -d N.E.K.O ]; then
  cp -a N.E.K.O/. neko-home/.local/share/N.E.K.O/ && rm -rf N.E.K.O
fi
[ -d ssl ] && cp -a ssl/. neko-home/ssl/ && rm -rf ssl

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
