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

入口脚本只在 `/home/neko/.local/share/N.E.K.O/config/core_config.json` 不存在时生成初始配置。API 环境变量不是实时通用覆盖；设置 `NEKO_FORCE_ENV_UPDATE` 会显式重新生成并覆盖该持久化初始配置，务必先备份。启动后请在 Web UI 确认。

| 宿主 | 容器 | 用途 |
| --- | --- | --- |
| `./neko-home` | `/home/neko` | 配置、角色、记忆、用户插件及其状态、功能数据、TLS 证书与私钥、OpenFang 运行状态 |
| `./logs` | `/app/logs` | 日志 |

`TZ` 默认是 `Asia/Shanghai`，可在 `.env` 改为任意 IANA 时区（例如 `Etc/UTC`）。升级前备份 `neko-home` 和 `logs`；严禁公开数据或私钥目录。不要用 `PLUGIN_CONFIG_ROOT`、`PLUGIN_PACKAGES_ROOT` 或 `PACKAGE_PROFILES_ROOT` 指向 `neko-home` 之外的路径，否则对应用户插件数据不会随容器持久化。

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
if ! MOUNTS=$(docker inspect neko --format '{{range .Mounts}}{{println .Destination}}{{end}}' 2>/dev/null); then
  echo "无法检查容器 neko；请停止迁移，切勿删除容器。" >&2
  exit 1
fi

# 第 2 步要用：真数据是从容器里捞出来的，就不能再被宿主机上那个旧目录覆盖
EXPORTED_APP_DATA=""

if printf '%s\n' "$MOUNTS" | grep -qx /home/neko; then
  echo "容器已按新布局挂载，没有待导出的内容"
else
  # 旧入口脚本把初始配置写到 /app/config；即使应用数据本身已挂载，API 配置
  # 仍可能只在容器可写层。删容器前先用它覆盖新生成的默认配置。
  if ! LEGACY_CORE_CONFIG_STATE=$(docker exec neko sh -c 'if [ -f /app/config/core_config.json ]; then printf present; elif [ -e /app/config/core_config.json ]; then printf invalid; else printf missing; fi'); then
    echo "无法检查旧的初始配置；请停止迁移。" >&2
    exit 1
  fi
  case "$LEGACY_CORE_CONFIG_STATE" in
    present)
      if ! docker cp neko:/app/config/core_config.json ./neko-home/.local/share/N.E.K.O/config/core_config.json; then
        echo "旧初始配置导出失败；请停止迁移，切勿删除容器。" >&2
        exit 1
      fi
      ;;
    missing) echo "（容器内没有旧的 /app/config/core_config.json）" ;;
    *) echo "旧初始配置不是普通文件；请停止迁移。" >&2; exit 1 ;;
  esac

  # 应用数据先导，这部分丢了找不回来。容器没把数据目录挂出去，就说明它只存在于
  # 容器可写层。
  if ! printf '%s\n' "$MOUNTS" | grep -qx /home/neko/.local/share/N.E.K.O; then
    if ! docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/; then
      echo "应用数据导出失败；请停止迁移，切勿删除容器。" >&2
      exit 1
    fi
    EXPORTED_APP_DATA=1
  fi
  # 只有源目录确实不存在时，OpenFang 状态才可忽略；检查或复制失败都可能导致状态丢失。
  if ! OPENFANG_STATE=$(docker exec neko sh -c 'if [ -d /home/neko/.openfang ]; then printf present; elif [ -e /home/neko/.openfang ]; then printf invalid; else printf missing; fi'); then
    echo "无法检查 OpenFang 状态；请停止迁移。" >&2
    exit 1
  fi
  case "$OPENFANG_STATE" in
    present)
      if ! docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/; then
        echo "OpenFang 状态导出失败；请停止迁移，切勿删除容器。" >&2
        exit 1
      fi
      ;;
    missing) echo "（容器内没有 .openfang）" ;;
    *) echo "OpenFang 状态不是目录；请停止迁移。" >&2; exit 1 ;;
  esac
fi
```

**确认第 1 步成功后再往下。** `docker compose down` 会删除容器，而在宿主 `N.E.K.O/` 为空的情况下，容器是那部分数据唯一的副本——导出若因权限、磁盘满或 daemon 没起而失败，请先解决再继续。

```bash
# 2. 停容器，再把宿主机上的旧目录按内容合并。若已经用新布局启动过一次，目标目录
#    已经存在（还带一张新生成的自签证书），直接 mv 会把旧目录套进去多一层。
#    同名文件以旧数据为准。
docker compose down
# 合并迁移文件时使用当前宿主用户；新容器启动后会将运行时状态恢复为 uid/gid 1000，
# 因此宿主账号不是 1000 时，之后备份或编辑可能需要 sudo（或配置匹配的组权限）。
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

`./logs` 不受影响。容器内的应用用户固定为 uid/gid **1000**；宿主账号也是 1000 时通常可直接管理 `neko-home/`，其他账号在容器启动后可能需要 `sudo` 或配置合适的组/ACL。
:::

当前 Compose 没有 `build:`，旧的 `docker compose build` 说法无效。本地构建应在仓库根目录执行：

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

随后设置 `NEKO_IMAGE`。入口脚本生成的是自签名证书，不等于公网可信 TLS。诊断用 `docker compose ps`、`docker logs neko` 和 `curl -f http://127.0.0.1:48911/health`。
