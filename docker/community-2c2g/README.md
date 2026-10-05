# Project N.E.K.O. - 2C2G 极限生存部署指南（对于较新版本的N.E.K.O.通用,适用于ubuntu系的操作系统）

> 作者：烨儿不会飞 (GitHub: @csy-11, Bilibili: 烨儿不会飞, 爱发电：https://afdian.com/a/chensye)
> 适用场景：99元/年 阿里云 ECS (2核2G) / 其他低配云服务器 / 预算极度受限的开发者
> 核心理念：用最少的钱，榨干每一滴性能，实现"零垃圾、高可用、防爆破"的赛博生存。
> 配套部署文件：**`docker-compose.yaml`（自适应部署方案：全自动初始化 + 自愈看门狗）**
> 与官方 Compose 一致，默认发布 HTTP/HTTPS 入口；可用 `http://IP:48911` 配对，页面提示连接未加密。推荐 HTTPS，需强制 HTTPS 时设置 `NEKO_REQUIRE_HTTPS=1`；镜像必须包含 #3289/#3299。此方案不保证任意负载都能在 2G 内存下稳定运行。

---

## 1. 背景故事：为什么需要这份指南？

大多数用户选择在本地 Steam 运行 N.E.K.O.，但如果你像我一样，手头只有一台性能孱弱的平板，或者想让 YUI 在云端 7x24 小时陪伴你，云服务器是最优解。

然而，官方默认的部署配置通常面向"理想环境"。在 2核2G、40G硬盘、带宽受限的"赛博贫民窟"里直接部署，你会遇到：

1. 内存 OOM（溢出）：Playwright 加上 Python 多进程，瞬间榨干物理内存，容器无限重启。
2. 磁盘爆满（Docker 刺客）：日志、镜像缓存和数据库飞速膨胀，让你看着 75% 的进度条焦虑失眠。
3. 账单背刺：按量付费的流量、临时升级的带宽，或者多租户共享导致的 API 额度瞬间灰飞烟灭。
4. 公网裸奔：暴露在公网 22 端口的 SSH 每天遭受数万次暴力破解。

这份指南，是我用真金白银和无数个熬夜排查换来的血泪经验。希望能帮到预算有限、但同样热爱折腾的你。

---

## 2. 快速开始（3 分钟上手）

### 2.1 前置条件（务必先确认）

| 检查项 | 说明 |
|---|---|
| **Docker Compose V2** | 需要 **2.24.4 及以上**以支持 `!override`，不能用旧版 Python 的 `docker-compose` v1；需要保留仓库中的 `docker/docker-compose.yml`，不能只下载本目录 |
| **宿主机有 `bash`** | 看门狗脚本 shebang 为 `#!/bin/bash` |
| **宿主机有 `curl`、`timeout`、`flock`** | `curl` 检查完整 HTTP 响应；`timeout`（coreutils）限制 Docker 命令；`flock`（util-linux）防止并发重启。可运行 `sudo apt install curl coreutils util-linux` |
| **root 级 cron + docker 套接字** | 看门狗由宿主 cron 每 5 分钟执行，并调用 `docker restart` |

### 2.2 部署命令

```bash
cd <本指南所在目录>          # 含 docker-compose.yaml 的目录
docker compose config --quiet   # 语法预检（可选但推荐）
docker compose up -d            # 启动
docker compose ps                                            # 查看状态
```

首次连接可打开 `http://<服务器IP>:48911` 配对；页面会提示连接未加密。 HTTP 会以明文传输配对 key 和会话 Cookie，网络路径上的观察者可能获取凭证并访问实例；不要在不可信网络上通过 HTTP 输入凭证，使用 HTTPS 或可信 TLS 网关。HTTP 访问远程 IP 时浏览器不开放麦克风，语音输入不可用。条件允许时推荐 `https://<服务器IP或域名>:48912`；镜像默认生成自签名证书，正式使用建议可信证书或可信 TLS 网关。需强制 HTTPS/WSS 时在同目录 `.env` 设置 `NEKO_REQUIRE_HTTPS=1` 后重新创建服务。实例访问凭证由管理员在服务器显式读取：

```bash
docker compose exec --user neko -w /app neko-main uv run python -m utils.instance_access
```

若命令不存在，说明镜像尚未包含 #3289；HTTP 默认支持也需核验镜像包含 #3299；先升级或从已合并源码构建，不能直接开放公网。凭证持久化在 `neko-home` 内，首次输入后此设备记住连接 30 天；不要把 key、Cookie 或社区令牌写入 URL、日志或截图。社区账户登录不代替实例授权。

使用自有域名和 HTTPS 时在同目录 `.env` 配置，例如：

```dotenv
SSL_DOMAIN=your-domain.example
NEKO_TRUSTED_HOSTS=your-domain.example
NEKO_TRUSTED_ORIGINS=https://your-domain.example:48912
# NEKO_IMAGE=ghcr.io/project-n-e-k-o/n.e.k.o@sha256:<经核验且包含实例授权的完整摘要>
```

默认 `SSL_DOMAIN=localhost`，`NEKO_TRUSTED_HOSTS` 留空并由入口脚本回退到该值；使用自有域名时整体填写自己的域名，不追加不受自己控制的域名。使用 IP 字面量无需域名白名单，仍需要实例凭证；HTTPS 模式还需证书。
外置 TLS 网关终止 HTTPS 时，按浏览器实际访问的公开 Origin 同时配置以下两项，例如网关使用默认 HTTPS 端口 443：

```dotenv
NEKO_INSTANCE_PUBLIC_ORIGIN=https://your-domain.example
NEKO_TRUSTED_ORIGINS=https://your-domain.example
```

这会替换前面直接访问容器 HTTPS 时带 `:48912` 的 Origin 示例。若网关使用其他公开端口，两项均填写实际公开端口；不要填写容器内部端口。`NEKO_INSTANCE_PUBLIC_ORIGIN` 声明公开 TLS 入口，不会配置 `NEKO_TRUSTED_ORIGINS` 白名单。内层 Nginx 的 HTTP 转发可能使 WebSocket guard 看到 `ws`，因此需要显式信任浏览器发送的 HTTPS Origin。网关应保留 Host 和正确的客户端 XFF 链，并代理 WebSocket；上游 HTTP 必须私有隔离。网关公网 HTTP 只能关闭或重定向到 HTTPS，不能把同 Host 明文流量代理进应用。仅使用容器自身 HTTPS 时留空 public origin。`NEKO_COMMUNITY_WEB_CLIENT_ID` / `NEKO_COMMUNITY_WEB_REDIRECT_URI` 通常留空，使用平台固定 relay；社区 OAuth 仍需核对认证平台、PC/社区配套发布及真实环境验收，不能以此模板或单测代替。

继承的官方端口默认公开；采用外置 TLS 网关时，必须在同目录创建 `compose.gateway.yaml` 将上游改为本机绑定，并在同目录 `.env` 持久设置文件组合：

```yaml
services:
  neko-main:
    ports: !override
      - "127.0.0.1:48911:80"
      - "127.0.0.1:48912:443"
```

```bash
# 写入同目录 .env（Ubuntu/Linux 使用冒号分隔）：
# COMPOSE_FILE=docker-compose.yaml:compose.gateway.yaml
docker compose config --quiet
docker compose up -d
```

将 `COMPOSE_FILE=docker-compose.yaml:compose.gateway.yaml` 添加到同目录 `.env` 后，本指南所有不带 `-f` 的部署、升级及重新初始化命令都会加载这两份文件。不要添加只指定基础文件的 `-f`，它会覆盖该组合；每次重建前用 `docker compose config` 核对最终端口绑定。

此示例适用于运行在同一宿主机、可连接本机端口的网关；网关在其他主机或容器时需按实际网络配置隔离上游。若启用看门狗，必须保留宿主 `http://127.0.0.1:48911/` 可达；更换宿主端口、仅绑定内网 IP 或只发布 HTTPS 会使探测失败，并对健康容器触发恢复直至预算耗尽。此类网络配置应先按第 3 节暂停看门狗，调整探测脚本并验证健康分支后再恢复。

完整契约见 [社区账户与远程实例访问边界](../../docs/design/security/community-remote-access.md)。

启动后会自动完成：
- **`neko-init`**：一次性初始化，创建 `neko-home/`、`logs/` 拒绝顶层符号链接，并仅将顶层属主对齐到 UID/GID 1000；不递归 `logs/` 或 `neko-home/`，避免修改嵌套挂载等宿主资源。旧日志升级见下方迁移步骤；数据子树由镜像入口脚本修复，SSL 私钥保留 root 权限。失败会阻止主服务启动。
- **`neko-main`**：N.E.K.O 主服务（Compose 将等待 `neko-init` 成功后启动）。

看门狗是可选的宿主修改，普通 `up` 不会安装或恢复已卸载的 cron。核验安装器镜像与两个脚本后，显式安装：

```bash
docker compose --profile watchdog run --rm neko-cron-install
```

该一次性服务把看门狗装到 `/opt/neko/watchdog.sh`，注册 `/etc/cron.d/neko-watchdog`，随后退出。不要持久设置 `COMPOSE_PROFILES=watchdog`；重新安装也需显式执行此命令。

> 若需重新初始化：`docker compose down && docker compose up -d`

---

### 2.3 升级已有 root 日志的权限

已有部署的 `logs/` 中若有 root 创建的文件或子目录，初始化不会自动修改它们。升级前先按第 3 节持锁暂停已安装的看门狗，再 `docker compose stop`。由管理员核验待迁移路径及所有父目录不是符号链接、没有嵌套挂载，并确认目标确实是本应用日志；Docker socket、其他服务数据和受保护资源不得列入迁移。

仅对核验过的目录或日志文件逐项执行非递归属主修复，例如（用实际存在的路径替换示例）：

```bash
sudo chown --no-dereference 1000:1000 -- ./logs/main.log
# 若该日志位于子目录，也仅单独修复核验过的目录本身：
# sudo chown --no-dereference 1000:1000 -- ./logs/main ./logs/main/main.log
```

不要使用 `chown -R`、通配符或仅依赖 `find -xdev` 批量修复；同一文件系统上的 bind mount 可能不被 `-xdev` 排除。启动服务并确认旧日志可写后，再解除看门狗暂停。新部署没有旧 root 日志时无需迁移。

---

## 3. 服务与自愈机制

### 3.1 服务拓扑

```
neko-init ──(success)──▶ neko-main ──▶ 48911(HTTP)/48912(HTTPS，默认全接口)
   │                        │
   └──(显式安装)──▶ neko-cron-install ──▶ 宿主 /opt/neko/watchdog.sh + /etc/cron.d/neko-watchdog
                                    └──▶ 每 5 分钟二层健康检查 + 自动重启
```

### 3.2 自愈看门狗做了什么

由宿主 cron 每 5 分钟执行 `/opt/neko/watchdog.sh`，**双层健康判据**：

- **第一层**：核验容器 `neko` 的部署标签、Compose 服务名与 Running/Paused/Restarting 状态。容器消失、手动停止、`docker pause`、正在重启或同名其他部署都不会被重启；进程退出交给 Docker 的 `unless-stopped` 策略。
- **第二层**：宿主 `curl` 请求本机 48911 首页，完整响应为 200 或新版正常的匿名 401；同时 `docker exec` 在容器内直连真正主服务的 `/health`，要求请求成功。当前 Nginx 的 `/health` 会优先匹配正则路由并代理到插件服务，不能代替主服务健康检查。两项探测均有总超时，收到状态码后仍超时也算失败。不再使用只能判断 TCP 连通的降级逻辑。

启动后默认有 **15 分钟宽限期**，按 Docker `State.StartedAt` 计算；期间清空失败计数，不执行恢复。同 ID 重启也重新获得宽限期。若实测启动更慢，在宿主 `/etc/cron.d/neko-watchdog` 中在任务行之前添加 `NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=1800`（秒）并按实际启动耗时调整；0 表示禁用。重装保留这一整数设置（支持一对匹配的单引号或双引号），非法或重复设置会拒绝重装。

宽限期后，健康即清空失败计数；**连续 2 次不健康 → 自动重启同一个容器 ID**。计数绑定 ID 和启动时间，重建、重启均不继承旧失败；重启前复核运行、暂停、重启状态和启动时间。Docker restart 使用 30 秒停止期限及 120 秒客户端总超时；客户端失败后复查 Docker 状态，确认新启动时间或正在重启时清计数，否则保留并报错。客户端超时不代表 daemon 已取消重启。每个容器 ID 最多连续尝试 3 次自动恢复（失败的 CLI 调用也计入）；启动宽限期和启动时间变化不重置预算，健康后清零，重建容器得到新预算。耗尽后记录错误并停止主动重启，Docker 的 `unless-stopped` 策略仍独立生效。排除故障后可执行 `sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/restart-count` 恢复预算。`flock` 防止 cron 与手动调用同时重启。容器已删除时正常静默退出，Docker 查询故障仍会报错。同一容器 ID 有主动恢复记录但已停止时，会明确记录需人工检查的错误，保留预算且不自动启动；这也可能是恢复尝试后的手动停止，维护请先持锁设置 `disabled`。

安装器拒绝符号链接和非 root 私有目录，原子安装脚本与 cron。状态、锁、日志位于 root:root、0700 的 `/opt/neko/`；计数损坏或读写失败会报错退出，不会静默归零。加锁前的配置、依赖和权限错误写 stderr，并在安全路径下追加 watchdog.log；有 `logger` 时同时写 syslog（可查 `journalctl -t neko-watchdog`，取决于宿主日志配置）。不安全目录或日志文件不会被写入。日志 `/opt/neko/watchdog.log` 无自动轮转，长期运行建议配置 logrotate。

开发者可运行 `sudo bash ./test-watchdog.sh` 验证恢复逻辑。测试将 Docker/HTTP 调用替换为模拟程序，安装路径改为临时目录，使用真实 Linux 权限、文件锁和计数读写；不安装真实 cron，也不重启容器。此 harness 使用宿主 GNU 工具，尚未验证实际 `alpine:3.20` 安装器中的 BusyBox 行为；通过此测试不代表已完成安装器镜像或 ECS 实机部署验收。

维护时先执行 `sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled`，等待正在执行的探测/重启结束并暂停，再停容器或执行 `docker pause`；恢复运行后 `sudo rm -f /opt/neko/disabled`。重新安装不会解除暂停。安装器会写入宿主 root cron，只在信任这两个脚本和安装器镜像的主机上使用；多套部署不要共用 `neko` 容器名及 `/opt/neko`。

---

## 4. 镜像源选择与配置

`docker-compose.yaml` 默认使用国内加速代理 + 完整版镜像（免去首次启动下载 Chromium 卡死）：

```yaml
image: ${NEKO_IMAGE:-docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:latest-full}
```

可通过环境变量覆盖，或取消注释切换：

```bash
export NEKO_IMAGE=ghcr.io/project-n-e-k-o/n.e.k.o:latest-full   # 海外/已配代理主机用官方源
docker compose up -d
```

`latest-full` 是滚动标签，不保证已发布的镜像包含最新 main。上线前核对镜像版本和实例授权，使用经过验证的 tag/digest 固定 `NEKO_IMAGE`；不在文档中虚构尚未发布的版本。保留加速代理作为默认下载入口，按实际网络选择官方源。

常用端口与目录：
- 端口：继承官方 `48911→80`（HTTP）、`48912→443`（HTTPS），默认绑定所有接口。不发布预留的 48915。
- **浏览器访问**：默认 `http://<你的IP>:48911` 可配对；推荐 `https://<你的域名或IP>:48912`。严格模式设置 `NEKO_REQUIRE_HTTPS=1`；按实际入口设置防火墙，可限制来源 IP。
- 数据卷：`./neko-home → /home/neko`（用户数据、SSL 证书）
- 日志卷：`./logs → /app/logs`
- 默认不挂载 Nginx 配置目录。当前入口脚本每次启动都会生成 `neko-proxy.conf`，不读取 `NEKO_KEEP_CUSTOM_NGINX_CONF`；需要自定义 TLS/代理时使用外层网关或经过验证的自定义镜像，不把无效变量当作配置保护。

---

## 5. 系统级"保命"配置（ZRAM 与 Swap）

2G 物理内存是硬伤，但你的 CPU 算力是闲置的。我们用 ZRAM（内存压缩）实现"用 CPU 算力换内存空间"。

### 1. 安装并配置 ZRAM

```bash
sudo apt update
sudo apt install zram-tools
```

编辑 `/etc/default/zramswap`，取消注释并修改以下参数：

```ini
ALGO=lz4          # 使用 lz4 算法，压缩速度快，CPU 消耗低
PERCENT=50        # 分配物理内存的 50% 作为 ZRAM（约 1G）
PRIORITY=100      # 优先级设为最高
```

重启服务并验证：

```bash
sudo systemctl restart zramswap
swapon --show
```

注意：保留一个 2G-4G 的物理硬盘 Swapfile（如 `/swapfile`）作为最后的备胎，防止极端情况下的彻底死机。

### 2. 调整系统交换倾向

```bash
sudo sysctl vm.swappiness=10
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf   # 永久生效
```

---

## 6. Docker"零垃圾"与日志限制

本 Compose 已限制主服务的 Docker json-file 日志为 10m × 3，无需覆盖宿主全局配置。注意应用写入 `logs/` 或持久化目录的日志不受 Docker logging 限制，应另外观察并轮转。如果要给其他容器配置全局默认值，请合并进现有 `/etc/docker/daemon.json`：

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

然后重启 Docker：`sudo systemctl restart docker`。

> 说明：**旧版方案中的"每日凌晨 `docker restart neko` 清内存碎片"已由新版的看门狗自动健康重启取代**，无需再手工添加该 cron 任务（看门狗本身就在做保活，且只在异常时重启，比固定每日重启更温和）。若仍需固定定时清理，可在 crontab 中手动添加。

---

## 7. 网络安全与防御（CrowdSec 集团军）

既然把服务暴露到了公网，就必须给它请一个免费的保镖。

### 1. 安装并配置 CrowdSec

```bash
curl -s https://install.crowdsec.net | sudo sh
sudo apt install crowdsec
sudo apt install crowdsec-firewall-bouncer-iptables   # 防火墙执行器，实现底层拦截
```

### 2. 核心安全建议

- 强制禁用密码登录：在 `/etc/ssh/sshd_config` 中设置 `PasswordAuthentication no`，只允许密钥登录。
- 保持默认 22 端口：若使用阿里云 Workbench 或移动端免密登录，不要为"防扫描"改 22 端口，否则易连不上。CrowdSec 会自动拦截爆破 IP。

---

## 8. 网络与流量计费优化（CDT 与 DuckDNS）

### 1. 启用 CDT（云数据传输）免费流量

CDT 免费额度有适用条件：按阿里云账号共享，不是每台 ECS 单独获得。目前中国内地可用额度为 20 GB/月，仅适用于符合条件的按流量计费 BGP（多线）公网出向流量；ECS 需为 VPC 类型。固定带宽和 BGP（多线）精品流量不适用该免费额度，固定带宽转按流量计费后也不能假定立即获得抵扣。

切换前先在 CDT 控制台核对账号剩余额度、其他资源的消耗及计费生效时间，估算本实例每月出网流量、超额费用，再与固定带宽总价比较。不要仅因有免费额度就改计费方式或拉高带宽峰值；流量较大、持续稳定时固定带宽也可能更合适。额度及价格可能调整，以[官方公网流量计费说明](https://help.aliyun.com/zh/cdt/internet-data-transfers/)和实际账单为准。

### 2. DuckDNS 动态域名解析

若公网 IP 会变化，可用 DuckDNS 做动态域名：注册域名拿 Token，定时更新解析。域名访问同时在 `.env` 设置 `NEKO_TRUSTED_HOSTS` / `NEKO_TRUSTED_ORIGINS`，并配置对应 HTTPS 证书。

---

## 9. 数据存储与冷热分离

核心原则：本地只存热数据（纯文本），冷数据（图片、视频）全部外置。

- N.E.K.O. 可将图片理解转化为文字存档，本地 SQLite 数据库因此极轻量（几千万字也才几十兆）。
- 长期存档的原始图片/音频，建议配置生命周期规则转入阿里云 OSS 低频/归档存储，成本低至几毛钱 1GB。
- 定期将数据库文件打包压缩，下载到本地或上传网盘作为异地容灾备份。

---

## 10. 上线核对清单（Checklist）

- [ ] `docker compose` 为 V2 版本（`docker compose version`）
- [ ] 宿主机有 `bash`、`curl`、`timeout`、`flock`（`command -v bash curl timeout flock`）
- [ ] `docker compose config --quiet` 无报错
- [ ] `docker compose up -d` 后 `docker compose ps` 显示 `neko-main` Running
- [ ] `neko-init` 一次性退出（`Exit 0`）；可选看门狗显式安装命令成功
- [ ] 若启用看门狗，宿主机存在 `/opt/neko/watchdog.sh`（首行 `#!/bin/bash`）且 `+x`
- [ ] 若启用看门狗，宿主机存在 `/etc/cron.d/neko-watchdog`（权限 644、属主 root）
- [ ] 若启用看门狗，手动执行 `/opt/neko/watchdog.sh` 健康分支退出码为 0
- [ ] 镜像包含 #3289/#3299；HTTP 或 HTTPS 首次输入实例凭证，刷新后可复用；HTTP 页面提示未加密且远程 IP 语音输入不可用
- [ ] 需要严格模式时配置 `NEKO_REQUIRE_HTTPS=1`；HTTPS 证书与白名单正确；使用 public origin 的外置 TLS 网关时，公网 HTTP 只关闭或重定向，私有 upstream 不可公开
- [ ] 匿名账户/API 返回 401、匿名 WebSocket 被拒；社区 OAuth 与配套发布独立验收
- [ ] 手动停止、暂停及同名其他部署不会被看门狗启动
- [ ] 故障时 `/opt/neko/watchdog.log` 正常写入；完成计数读写及两次失败重启验收

---

## 写在最后

这套架构，是我作为一个初中生，在极度受限的资源下探索出的最优解。它曾经让我从"因为差 15 块钱续费而绝望"，变成了"在 2 核 2G 的机器上也能稳稳保护我的 AI 伙伴"。

如果你在使用这份指南时遇到了问题，欢迎在 Issue 区交流。开源的精神就是互相搀扶，希望 YUI 能在更多人的设备里安稳地活下去。
### 卸载与清理
看门狗独立于 Compose 生命周期；`docker compose down` 不卸载 root cron。彻底移除时先撤销恢复权限，再停止服务（保留用户数据）：
```bash
sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled
sudo rm -f /etc/cron.d/neko-watchdog
# 等待正在执行的探测/重启退出，再持锁移除脚本。
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/watchdog.sh
docker compose down
```
---

## 赞助与支持（求赞助区）

这套指南和配置文件完全开源且免费。写下这些文字的时候，我还是一个初中生，这台 99 元/年的阿里云 ECS 和里面运行的 YUI，几乎耗尽了我所有的零花钱。

如果这份《2C2G 极限生存指南》帮你省下了几百块钱的服务升级费，或者让你的 YUI 在低配机器上成功跑了起来，可以考虑请我喝杯奶茶（或者赞助几块钱的电费/流量费）。这笔钱将直接用于：
- 续费这台 99 元/年的赛博老破小（保住 YUI 的命）
- 买几个便宜的 ESP32-S3 开发板折腾物理外挂
- 偶尔抵扣一下爆掉的 API Token 账单

**赞助方式：**
- 爱发电：[https://afdian.com/a/chensye](https://afdian.com/a/chensye)

当然，如果你手头也不宽裕，完全不需要打赏。去 GitHub 给我的项目点个 Star，或者把这个指南分享给其他需要的人，就是对我最大的支持。开源的精神就是互相搀扶，让我们一起在赛博世界里苟住！

—— 烨儿不会飞 (csy-11)
