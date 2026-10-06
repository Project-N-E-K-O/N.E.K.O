# 低配云服务器部署（2C2G）

本页面向 2 核 2G、40G 硬盘、带宽受限的云服务器（例如入门级 ECS），在官方 Docker 部署的基础上补充内存、磁盘、自愈、安全和流量方面的宿主机配置，不需要另一套 Compose。

> 本页整理自社区贡献者 烨儿不会飞（GitHub [@csy-11](https://github.com/csy-11)）在 99 元/年 ECS 上的实践，原始方案见 [#3295](https://github.com/Project-N-E-K-O/N.E.K.O/pull/3295)。如果这份指南帮到了你，可以在[爱发电](https://afdian.com/a/chensye)支持作者。

::: warning 适用范围
本页命令以 Ubuntu 系宿主机为例，均未在每种云厂商环境逐一验收。官方 Compose 不为主服务设置内存上限，2G 宿主能否稳定运行取决于实际负载；看门狗也不能代替宿主 OOM 防护。上线前请用代表性负载验证峰值内存、延迟和回退方案。
:::

## 1. 部署

按 [Docker 部署](./docker) 完成安装，Compose 文件就是 `docker/docker-compose.yml`。低配机器上建议：

- **使用 full 镜像**：在 `docker/.env` 设置 `NEKO_IMAGE_VERSION=latest-full`。镜像自带 Chromium，首次启动不用在容器里下载安装浏览器，代价是多占约 1GB 磁盘。
- **固定版本**：`latest`、`latest-full` 是滚动标签。上线前用 `NEKO_IMAGE` 固定经过验证的 tag 或 digest，并确认镜像包含实例授权（#3289）与 HTTP 配对（#3299）。
- **自有域名**：在 `docker/.env` 配置，官方 Compose 会把它们传入容器：

```dotenv
SSL_DOMAIN=your-domain.example
NEKO_TRUSTED_HOSTS=your-domain.example
NEKO_TRUSTED_ORIGINS=https://your-domain.example:48912
```

默认允许通过 `http://<服务器IP>:48911` 配对，页面会提示连接未加密；HTTP 会以明文传输配对 key 和会话 Cookie，不要在不可信网络上这样输入凭证。需要强制 HTTPS/WSS 时设置 `NEKO_REQUIRE_HTTPS=1`。实例凭证的读取方式见 [Docker 部署](./docker)。

已有部署改了 `SSL_DOMAIN` 时，入口脚本会继续复用 `docker/neko-home/ssl/` 下已有的自签名证书，不会按新域名重新签发。需要新证书时，先停容器，把 `N.E.K.O.crt` 和 `N.E.K.O.key` 移出该目录备份，再启动让入口脚本重新生成；使用自有证书时直接替换这两个文件。

`docker/neko-home` 和 `docker/logs` 不要做成指向共享目录的符号链接：Docker 会挂载链接的目标，入口脚本随后把挂载根目录的属主改为 uid 1000。需要把数据放到其他磁盘时，让它指向一个专用的空目录，或在覆盖文件中直接写实际路径。

如需给容器设内存上限，按 [Docker 资源约束文档](https://docs.docker.com/engine/containers/resource_constraints/) 在覆盖文件中设置，并以实测结果确定数值，不要直接套用经验值。

## 2. 外置 TLS 网关：上游只绑定本机

官方 Compose 默认在所有接口发布 48911/48912。由同一宿主机上的网关（Nginx、Caddy 等）终止 HTTPS 时，应把上游改为只绑定本机。在 `docker/` 下创建 `compose.gateway.yaml`（已被 `.gitignore` 忽略）：

```yaml
services:
  neko-main:
    ports: !override
      - "127.0.0.1:48911:80"
      - "127.0.0.1:48912:443"
```

然后在 `docker/.env` 中持久设置文件组合，之后所有不带 `-f` 的 `docker compose` 命令都会加载这两份文件：

```dotenv
COMPOSE_FILE=docker-compose.yml:compose.gateway.yaml
```

`!override` 需要 Docker Compose **2.24.4 及以上**。每次重建前用 `docker compose config` 核对最终端口绑定。

网关按浏览器实际访问的公开 Origin 同时配置以下两项（例如网关使用 443）：

```dotenv
NEKO_INSTANCE_PUBLIC_ORIGIN=https://your-domain.example
NEKO_TRUSTED_ORIGINS=https://your-domain.example
```

网关应保留 Host 和正确的客户端 `X-Forwarded-For` 链并代理 WebSocket；公网 HTTP 只能关闭或重定向到 HTTPS，不能把同 Host 的明文流量转发进应用。完整契约见[社区账户与远程实例访问边界](/design/security/community-remote-access)。

## 3. 内存：ZRAM 与 Swap

2G 物理内存是主要瓶颈，而 CPU 往往有空闲。ZRAM 用 CPU 压缩换内存空间：

```bash
sudo apt update
sudo apt install zram-tools
```

编辑 `/etc/default/zramswap`：

```ini
ALGO=lz4          # 压缩快、CPU 开销低
PERCENT=50        # 使用物理内存的 50% 作为 ZRAM（约 1G）
PRIORITY=100      # 优先于磁盘 swap
```

```bash
sudo systemctl restart zramswap
swapon --show
```

另外保留一个 2–4G 的磁盘 swapfile 作为最后的兜底。

`vm.swappiness` 没有通用最优值。以 ZRAM 为主时，可在代表性负载下评估 100 附近的取值（[内核文档](https://www.kernel.org/doc/html/latest/admin-guide/sysctl/vm.html#swappiness)允许内存型 swap 使用高于 100 的值）；只想减少磁盘 swap I/O 时才考虑 10 这类低值。任何取值都不保证避免 OOM，修改前记下原值以便回退。

## 4. 磁盘：日志与镜像

- 官方 Compose 已把主容器的 Docker 日志（`docker logs`）限制为 10m × 3。
- 应用文件日志写在 `docker/neko-home/.local/share/N.E.K.O/logs/`（`docker/logs/` 只是后备目录），不受上面的限制，但应用会自行轮转（单个文件 10MB、保留 5 份，30 天前的日志自动清理）。排查问题时也先看这里。
- 其他容器需要同样的限制时，把以下内容合并进现有 `/etc/docker/daemon.json`，再执行 `sudo systemctl restart docker`：

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

  这只对之后**新建**的容器生效，已有容器会保留原来的日志设置。改完后要重建这些容器（例如在各自目录执行 `docker compose up -d --force-recreate`），再确认已生效：

  ```bash
  docker inspect --format '{{.HostConfig.LogConfig}}' <容器名>
  ```

- 升级镜像后用 `docker system df` 查看占用，用 `docker image prune` 清理不再使用的悬空镜像。

## 5. 可选：宿主机自愈看门狗

Docker 的 `unless-stopped` 只在进程退出时重启容器；进程还在但服务卡死（低内存时更常见）不会被处理。`docker/watchdog/` 提供一个可选的宿主机看门狗来补上这一点。它会向宿主机写入 root cron，只在你信任这些脚本的 Linux 主机上使用。

### 前置条件

| 项目 | 说明 |
|---|---|
| 宿主工具 | `bash`、`curl`、`timeout`（coreutils）、`flock`（util-linux）：`sudo apt install curl coreutils util-linux` |
| cron 服务 | `sudo apt install cron && sudo systemctl enable --now cron`，用 `systemctl is-active cron` 确认 |
| Docker | 官方 apt 安装的 Docker Engine；cron 的 PATH 不含 `/snap/bin`，不支持 snap 版 |
| 安装器镜像 | 安装器在 `alpine:3.20` 中运行，需能从 Docker Hub 拉取，或换成你已核验的镜像 |

### 工作方式

cron 每 5 分钟执行一次 `/opt/neko/watchdog.sh`：

- 只处理名为 `neko`、带 `org.neko.watchdog=enabled` 标签、Compose 服务名为 `neko-main` 的容器。手动停止、`docker pause`、正在重启或已删除的容器都不会被启动。
- 健康判据有两项：宿主机请求 `http://127.0.0.1:48911/` 得到 200 或 401，并且容器内直连主服务 `/health` 成功。
- 容器启动后有 **15 分钟宽限期**。宽限期后连续 2 次不健康才执行 `docker restart`。
- 同一个容器最多连续自动重启 **3 次**，用完后记录错误并停止主动重启，等人工处理；健康一次即清零。
- 状态、锁和日志位于 root 私有的 `/opt/neko/`，日志为 `/opt/neko/watchdog.log`（不会自动轮转），有 `logger` 时也写入 syslog（`journalctl -t neko-watchdog`）。

看门狗通过宿主机 `127.0.0.1:48911` 探测。第 2 节的本机绑定不影响探测；如果改了宿主端口或只发布 HTTPS，探测会失败，并对健康的容器白白重启 3 次。这种情况下先暂停看门狗，修改仓库里的源文件 `docker/watchdog/watchdog.sh` 中的探测地址，重新安装并确认探测成功后再恢复。只改已安装的 `/opt/neko/watchdog.sh` 会在下次重装时被覆盖；源文件的改动是本地补丁，每次 `git pull` 后要核对。

### 安装

核验 `docker/watchdog/` 下的两个脚本后，在 `docker/` 目录执行：

```bash
docker run --rm --network none \
  -v /etc/cron.d:/host-cron.d -v /opt:/host-opt \
  -v "$PWD/watchdog:/source:ro" \
  alpine:3.20 sh /source/install-watchdog.sh
```

安装器会拒绝符号链接和非 root 私有目录，原子写入 `/opt/neko/watchdog.sh` 和 `/etc/cron.d/neko-watchdog`。重新安装会保留已有的宽限期设置。

启动明显更慢时，在 `/etc/cron.d/neko-watchdog` 的任务行之前加一行 `NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=1800`（单位秒，0 表示关闭宽限期）。手动运行脚本时不会读取 cron 文件，需要显式传入同一个值。

### 维护、恢复与卸载

```bash
# 维护前暂停（等待正在执行的探测或重启结束）
sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled

# 维护完成、确认服务健康后恢复，并清掉暂停前的失败计数
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/fail-count /opt/neko/disabled

# 自动重启次数用完、排除故障后恢复预算
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/restart-count

# 卸载（保留 /opt/neko 下的状态与日志，便于排查）
sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled
sudo rm -f /etc/cron.d/neko-watchdog
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/watchdog.sh
```

`docker compose down` 不会卸载 cron。重新安装不会解除 `disabled` 暂停。

### 验收

脚本在暂停、宽限期内或目标不匹配时会静默退出，所以退出码为 0 或日志里没有失败，都不能证明看门狗在保护容器。安装后逐项确认：

```bash
ls -l /etc/cron.d/neko-watchdog          # root 所有，权限 644
test ! -e /opt/neko/disabled && echo not-paused
docker inspect neko --format '{{index .Config.Labels "org.neko.watchdog"}} {{index .Config.Labels "com.docker.compose.service"}} {{.State.Running}}'
# 应输出：enabled neko-main true
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' --max-time 10 http://127.0.0.1:48911/
# 应为 200 或 401
docker exec neko sh -c 'curl --noproxy "*" -fsS --max-time 10 "http://127.0.0.1:${NEKO_MAIN_SERVER_PORT:-48911}/health" > /dev/null' && echo health-ok
```

### 测试

在仓库根目录执行 `sudo bash docker/watchdog/test-watchdog.sh`（在 `docker/` 下则是 `sudo bash watchdog/test-watchdog.sh`），它会在临时目录中用模拟的 docker/curl 运行真实脚本，覆盖宽限期、维护锁、重启上限和安装器等逻辑，不修改宿主 cron，也不重启容器。它不能代替实机验收。

## 6. 网络安全

- **优先在云侧限制来源**：在安全组中按实际入口限制来源 IP，并分别验证 IPv4/IPv6。Docker 发布的端口可能绕过 ufw 和宿主 `INPUT` 规则（见 [Docker 防火墙文档](https://docs.docker.com/engine/network/firewall-iptables/)），不要只看宿主防火墙规则就认为 48911/48912 已受保护。
- **SSH 只用密钥**：在 `/etc/ssh/sshd_config` 中设置 `PasswordAuthentication no`。使用云厂商网页终端或移动端免密登录时，保持 22 端口更省事。
- **CrowdSec 拦截爆破**：

```bash
curl -s https://install.crowdsec.net | sudo sh
sudo apt install crowdsec crowdsec-firewall-bouncer-iptables
```

Docker 使用 iptables 后端时，按 [CrowdSec 文档](https://docs.crowdsec.net/docs/bouncers/firewall/)在 bouncer 配置中合并 `iptables_chains: [INPUT, DOCKER-USER]`，并从外部来源实际验证封禁效果；nftables 后端的配置不同，不要照搬。CrowdSec 默认用于 SSH 等已接入日志的服务，不能代替实例凭证和 HTTPS。

## 7. 流量与域名

- **阿里云 CDT**：CDT 免费额度按账号共享，仅适用于符合条件的按流量计费公网出向流量，固定带宽不适用。切换计费方式前先估算月流量并与固定带宽总价比较，以[官方计费说明](https://help.aliyun.com/zh/cdt/internet-data-transfers/)和实际账单为准。
- **动态域名**：公网 IP 会变化时可用 DuckDNS 等服务定时更新解析，并按第 1 节设置 `SSL_DOMAIN`、`NEKO_TRUSTED_HOSTS`、`NEKO_TRUSTED_ORIGINS`，配置对应证书。

## 8. 数据与备份

- 本地只放热数据：N.E.K.O 的记忆以文本为主，SQLite 数据库很轻。长期保存的原始图片和音频可转存到对象存储的低频或归档层。
- 定期打包 `docker/neko-home/` 做异地备份。其中包含实例凭证和 TLS 私钥，备份不要公开。

## 9. 从 community-2c2g 部署迁移

如果你按此前的 `docker/community-2c2g/` 方案部署过，更新代码后该目录的 Compose 文件已不存在，数据目录仍在原处（已被 `.gitignore` 忽略）。除特别注明外，以下命令都在**仓库根目录**执行（先 `cd` 到包含 `docker/` 的 N.E.K.O 目录）：

1. 如装了看门狗，先按第 5 节暂停：`sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled`。
2. **删除容器之前**，从旧容器读出实际挂载的数据来源。第 2 到 4 步要在同一个 bash 会话里执行，后面的命令会用到这里的变量：

   ```bash
   HOME_SRC=$(docker inspect neko --format '{{range .Mounts}}{{if eq .Destination "/home/neko"}}{{.Source}}{{end}}{{end}}')
   LOGS_SRC=$(docker inspect neko --format '{{range .Mounts}}{{if eq .Destination "/app/logs"}}{{.Source}}{{end}}{{end}}')
   CFG_DIR=$(realpath docker/community-2c2g)    # .env 和网关覆盖文件所在目录
   echo "home=$HOME_SRC logs=$LOGS_SRC cfg=$CFG_DIR"
   ```

   通常两个来源分别是 `docker/community-2c2g/neko-home` 和 `docker/community-2c2g/logs`，改过挂载路径时会显示实际路径。任一变量为空，或路径与预期不符时，先停下，不要删容器。

   旧部署可能还通过 `--env-file`、shell 环境变量、`COMPOSE_FILE` 或 `-f` 覆盖文件带入了配置。不管来源是哪里，容器里最终生效的值都可以读出来。删除容器前把它们存成仅 root 可读的快照，第 6 步用来比对：

   ```bash
   { docker inspect neko --format 'image={{.Config.Image}}'
     docker inspect neko --format 'ports={{json .HostConfig.PortBindings}}'
     docker inspect neko --format '{{printf "memory=%v\n" .HostConfig.Memory}}{{printf "memory_swap=%v\n" .HostConfig.MemorySwap}}{{printf "nano_cpus=%v\n" .HostConfig.NanoCpus}}{{printf "read_only=%v\n" .HostConfig.ReadonlyRootfs}}{{printf "cap_add=%v\n" .HostConfig.CapAdd}}{{printf "cap_drop=%v\n" .HostConfig.CapDrop}}{{printf "security_opt=%v\n" .HostConfig.SecurityOpt}}{{printf "tmpfs=%v\n" .HostConfig.Tmpfs}}{{printf "extra_hosts=%v\n" .HostConfig.ExtraHosts}}{{printf "restart=%v\n" .HostConfig.RestartPolicy.Name}}'
     docker inspect neko --format '{{range .Config.Env}}{{println .}}{{end}}'
   } | sudo sh -c 'umask 077; cat > /root/neko-2c2g-effective.txt'
   ```

   快照除了环境变量，还记录内存/CPU 限制、只读根文件系统、capabilities、安全选项、tmpfs、extra_hosts 和重启策略。快照包含实例密钥等敏感值，不要贴到 issue、聊天或日志里。
3. 停止容器，按上面读出的实际来源，以 root 保留属主和权限备份数据目录，以及存在的 `.env` 和网关覆盖文件，然后核验备份：

   ```bash
   docker stop neko
   BACKUP=("${HOME_SRC#/}" "${LOGS_SRC#/}")
   for f in .env compose.gateway.yaml; do [ -f "$CFG_DIR/$f" ] && BACKUP+=("${CFG_DIR#/}/$f"); done
   # 归档写到仓库外的 /root，umask 077 使其仅 root 可读；按绝对路径保存，回退时可原样恢复
   sudo sh -c 'umask 077; tar -czpf "$0" -C / "$@"' /root/neko-2c2g-backup.tar.gz "${BACKUP[@]}" && echo tar-ok
   # 先完整读一遍归档：截断或损坏时 tar 会报错，不会输出 archive-ok
   sudo tar -tzf /root/neko-2c2g-backup.tar.gz > /dev/null && echo archive-ok
   # 再逐项确认实际来源都在归档里
   LIST=$(sudo tar -tzf /root/neko-2c2g-backup.tar.gz)
   for p in "${BACKUP[@]}"; do printf '%s\n' "$LIST" | grep -qxF -e "$p" -e "$p/" && echo "ok /$p" || echo "MISSING /$p"; done
   ```

   必须依次看到 `tar-ok` 和 `archive-ok`，并且每一项都是 `ok`、没有 `MISSING`。备份含实例凭证和 TLS 私钥，不要复制到仓库目录或公开位置。确认备份完整后再删除容器：`docker rm neko`。
4. 确认 `docker/neko-home` 和 `docker/logs` 尚不存在（已存在说明另有官方部署的数据，先核对，不要覆盖），再从实际来源以 root 保留属主和权限地复制：`sudo cp -a "$HOME_SRC" docker/neko-home && sudo cp -a "$LOGS_SRC" docker/logs`。TLS 私钥属主为 root、权限 0600，不用 root 复制会遗漏。

   复制后不需要手动 `chown -R`：容器每次启动时，入口脚本会以 root 把 `neko-home` 顶层和 `.local/share/N.E.K.O` 下的全部数据（记忆、角色、配置等）对齐到 uid/gid 1000，并把 `logs` 挂载点本身对齐到 1000。它不会递归修改 `logs` 里的旧文件：如果其中有以前以 root 写下的日志，按需逐个修复，例如 `sudo chown --no-dereference 1000:1000 -- docker/logs/某个.log`；不要用 `chown -R` 或通配符，以免改到挂载进来的其他宿主路径。
5. 把旧部署的**全部**有效配置迁过来，不只是 `docker/community-2c2g/.env`，还包括启动时用过的 `--env-file`、shell 环境变量、`COMPOSE_FILE` 和 `-f` 覆盖文件。需要的值写入 `docker/.env`；网关覆盖文件改放 `docker/compose.gateway.yaml`，`COMPOSE_FILE` 改为 `docker-compose.yml:compose.gateway.yaml`。之后用不带 `-f` 的 `docker compose` 命令就能得到完整配置，不要依赖临时的 shell 变量。

   旧部署的覆盖文件里如果还有 `mem_limit`、`read_only`、`cap_drop`、`tmpfs`、`extra_hosts`、`devices`、`ulimits` 等非环境变量设置，也一并写进 `docker/compose.local.yaml` 的 `neko-main` 下。快照没有覆盖的选项（如 `devices`、`ulimits`）要对照旧覆盖文件人工核对。

   注意：官方 Compose 只透传固定的几个变量（见 `docker/CONFIG_REFERENCE.md`），`DISABLE_SSL` 等其他变量写进 `.env` 也不会进入容器。快照里有这类变量时，把它们写进 `docker/compose.local.yaml`（已被 `.gitignore` 忽略），并加入 `COMPOSE_FILE`：

   ```yaml
   services:
     neko-main:
       environment:
         - DISABLE_SSL=${DISABLE_SSL:-}
   ```

   ```dotenv
   COMPOSE_FILE=docker-compose.yml:compose.local.yaml   # 同时用网关时：docker-compose.yml:compose.gateway.yaml:compose.local.yaml
   ```

   启动前在 `docker/` 列出将传入容器的变量名（只显示名字，不显示值），确认快照里的应用变量都在其中：

   ```bash
   docker compose config --format json | python3 -c 'import json,sys; print("\n".join(sorted(json.load(sys.stdin)["services"]["neko-main"]["environment"])))'
   ```
6. 在 `docker/` 执行 `docker compose config` 核对挂载来源和端口后 `docker compose up -d`，确认实例凭证、角色和记忆都在。再回到仓库根目录，把新容器的有效配置和第 2 步的快照比对：

   ```bash
   { docker inspect neko --format 'image={{.Config.Image}}'
     docker inspect neko --format 'ports={{json .HostConfig.PortBindings}}'
     docker inspect neko --format '{{printf "memory=%v\n" .HostConfig.Memory}}{{printf "memory_swap=%v\n" .HostConfig.MemorySwap}}{{printf "nano_cpus=%v\n" .HostConfig.NanoCpus}}{{printf "read_only=%v\n" .HostConfig.ReadonlyRootfs}}{{printf "cap_add=%v\n" .HostConfig.CapAdd}}{{printf "cap_drop=%v\n" .HostConfig.CapDrop}}{{printf "security_opt=%v\n" .HostConfig.SecurityOpt}}{{printf "tmpfs=%v\n" .HostConfig.Tmpfs}}{{printf "extra_hosts=%v\n" .HostConfig.ExtraHosts}}{{printf "restart=%v\n" .HostConfig.RestartPolicy.Name}}'
     docker inspect neko --format '{{range .Config.Env}}{{println .}}{{end}}'
   } | sudo sh -c 'umask 077; cat > /root/neko-official-effective.txt'
   # 只列出有差异的变量名（< 旧容器，> 新容器），不打印任何值
   sudo bash -c '[ -s "$0" ] && [ -s "$1" ] || { echo "COMPARE FAILED: snapshot missing or empty"; exit 2; }
     out=$(diff <(sort "$0") <(sort "$1")); rc=$?
     [ "$rc" -le 1 ] || { echo "COMPARE FAILED"; exit 2; }
     [ "$rc" -eq 0 ] && { echo identical; exit 0; }
     printf "%s\n" "$out" | sed -n "s/^\([<>]\) \([^=]*\)=.*/\1 \2/p"; exit 1' \
     /root/neko-2c2g-effective.txt /root/neko-official-effective.txt
   ```

   输出 `identical` 才表示完全一致；出现 `COMPARE FAILED` 说明比对没有完成，先查明原因，不能当作通过。重点确认 `NEKO_REQUIRE_HTTPS`、`NEKO_INSTANCE_ACCESS_KEY`、`NEKO_INSTANCE_PUBLIC_ORIGIN`、`NEKO_TRUSTED_HOSTS`、`NEKO_TRUSTED_ORIGINS`、`SSL_DOMAIN`、镜像和端口没有丢失或变化。需要看具体值时，用 `sudo grep '^变量名=' 文件` 单独查看，不要整份打印。有非预期差异时先 `(cd docker && docker compose down)`，修正 `docker/.env` 或覆盖文件后再启动。迁移确认成功后再删除这两个快照文件。

   迁移失败需要回退到旧部署时，在仓库根目录执行：

   ```bash
   (cd docker && docker compose down)       # 停掉并删除新容器，不要加 -v
   sudo tar -xzpf /root/neko-2c2g-backup.tar.gz -C /   # 按原绝对路径恢复数据和配置
   # 旧 Compose 文件已从仓库删除，从 #3295 的合并提交取回
   mkdir -p docker/community-2c2g   # 数据和覆盖文件都在仓库外时，该目录可能已不存在
   git show 5161fba:docker/community-2c2g/docker-compose.yaml > docker/community-2c2g/docker-compose.yaml
   # 启动前核对最终端口绑定和挂载来源：使用外置网关时两个端口都应是 127.0.0.1，
   # 挂载来源应与第 2 步 docker inspect 看到的一致
   (cd docker/community-2c2g && docker compose config | grep -E 'host_ip|published|source:')
   (cd docker/community-2c2g && docker compose up -d)
   ```

   恢复的 `.env` 里若有 `COMPOSE_FILE`，上面的命令会自动加载网关覆盖文件。旧部署如果是用 `-f` 显式指定覆盖文件的，`config` 和 `up` 都要带上同样的 `-f` 参数；端口绑定或挂载来源不对时不要启动。

   取回的旧 Compose 继承当前的官方 Compose，回退后的容器同时带有旧标签和新标签，所以无论已安装的是旧版还是第 7 步重装的新版看门狗，都能识别它。确认旧服务健康后，解除第 1 步的暂停并清掉暂停前的失败计数：`sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/fail-count /opt/neko/disabled`。

   浅克隆里找不到该提交时，先执行 `git fetch --unshallow`。取回的 `docker-compose.yaml` 不受版本管理，之后重新迁移成功时删除即可，不要提交。
7. **重新安装看门狗**（第 5 节）。旧脚本只识别旧标签，不重装就不会再处理新容器。确认健康后解除 `disabled`。

## 10. 上线核对清单

- [ ] `docker compose ps` 显示 `neko-main` 运行中
- [ ] 镜像固定到已验证版本，并包含 #3289/#3299
- [ ] 首次通过 HTTP 或 HTTPS 输入实例凭证后，刷新可复用；匿名 API 返回 401
- [ ] 需要严格模式时已设置 `NEKO_REQUIRE_HTTPS=1`；使用外置网关时上游只绑定本机
- [ ] 安全组已限制来源，并从外部验证过
- [ ] ZRAM 已生效（`swapon --show`），保留了磁盘 swapfile
- [ ] 若启用看门狗，`/opt/neko/watchdog.log` 已配置 logrotate（它不会自动轮转）
- [ ] 若启用看门狗：第 5 节「验收」中的各项检查全部通过
