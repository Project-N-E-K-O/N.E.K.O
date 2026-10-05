# Project N.E.K.O. - 2C2G 极限生存部署指南（对于较新版本的N.E.K.O.通用,适用于ubuntu系的操作系统）

> 作者：烨儿不会飞 (GitHub: @csy-11, Bilibili: 烨儿不会飞, 爱发电：https://afdian.com/a/chensye)
> 适用场景：99元/年 阿里云 ECS (2核2G) / 其他低配云服务器 / 预算极度受限的开发者
> 核心理念：用最少的钱，榨干每一滴性能，实现"零垃圾、高可用、防爆破"的赛博生存。
> 配套部署文件：**`docker-compose.yaml`（自适应部署方案：全自动初始化 + 自愈看门狗）**
> 默认只绑定本机入口。公网使用 HTTPS + 实例访问凭证；部署镜像必须包含 #3289 的实例授权实现。此方案不保证任意负载都能在 2G 内存下稳定运行。

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

为了方便新手，本目录附带 install_docker.sh（目前还在审查中），可在全新 Ubuntu 机器上一键安装 Docker、补全依赖并可选开启 ZRAM 内存优化。用法：
```bash
#cd到本指南目录：cd <本指南所在目录> 
sudo bash install_docker.sh --optimize --start
```

### 2.1 前置条件（务必先确认）

| 检查项 | 说明 |
|---|---|
| **Docker Compose V2** | 必须用新版 `docker compose`（**不能用**旧版 Python 的 `docker-compose` v1） |
| **宿主机有 `bash`** | 看门狗脚本 shebang 为 `#!/bin/bash`（健康探测用真实 HTTP，不用 `/dev/tcp`） |
| **宿主机有 `curl`、`timeout`、`flock`** | `curl` 检查完整 HTTP 响应；`timeout`（coreutils）限制 Docker 命令；`flock`（util-linux）防止并发重启。可运行 `sudo apt install curl coreutils util-linux` |
| **root 级 cron + docker 套接字** | 看门狗由宿主 cron 每 5 分钟执行，并调用 `docker restart` |

### 2.2 部署命令

```bash
cd <本指南所在目录>          # 含 docker-compose.yaml 的目录
docker compose -f "docker-compose.yaml" config --quiet   # 语法预检（可选但推荐）
docker compose -f "docker-compose.yaml" up -d            # 启动
docker compose ps                                            # 查看状态
```

> 本目录的 `docker-compose.yaml` 通过 `extends` 继承上一级官方 `docker/docker-compose.yml`，请保持该文件存在；官方后续更新会自动带入本方案，无需手工复制同步。

首次连接使用 `https://127.0.0.1:48912`。远程主机可先通过 SSH 转发 HTTPS：

```bash
ssh -L 48912:127.0.0.1:48912 <服务器用户>@<服务器地址>
```

在本机浏览器打开同一 HTTPS 地址。镜像默认生成自签名证书；公网使用前应部署可信证书或可信 TLS 网关。实例访问凭证由管理员在服务器显式读取：

```bash
docker compose exec --user neko -w /app neko-main uv run python -m utils.instance_access
```

若命令不存在，说明镜像尚未包含 #3289；先升级或从已合并源码构建，不能直接开放公网。凭证持久化在 `neko-home` 内，首次输入后此设备记住连接 30 天；不要把 key、Cookie 或社区令牌写入 URL、日志或截图。社区账户登录不代替实例授权。

公网部署前在同目录 `.env` 设置域名与 HTTPS 绑定，例如：

```dotenv
NEKO_HTTPS_BIND_IP=0.0.0.0
NEKO_TRUSTED_HOSTS=your-domain.example
NEKO_TRUSTED_ORIGINS=https://your-domain.example:48912
# NEKO_IMAGE=ghcr.io/project-n-e-k-o/n.e.k.o@sha256:<经核验且包含实例授权的完整摘要>
```

使用 IP 字面量无需域名白名单，但仍需要 HTTPS、证书与实例凭证。外置 TLS 网关终止 HTTPS 时，设置 `NEKO_INSTANCE_PUBLIC_ORIGIN=https://your-domain.example`，保留 Host 和正确的客户端 XFF 链，代理 WebSocket；上游 HTTP 必须私有隔离。网关公网 HTTP 只能关闭或重定向到 HTTPS，不能把同 Host 明文流量代理进应用。仅使用容器自身 HTTPS 时留空 public origin。`NEKO_COMMUNITY_WEB_CLIENT_ID` / `NEKO_COMMUNITY_WEB_REDIRECT_URI` 通常留空，使用平台固定 relay；社区 OAuth 仍需核对认证平台、PC/社区配套发布及真实环境验收，不能以此模板或单测代替。

> ⚠️ **DNS rebinding 防护**：本方案**不再默认信任任何公网域名**（旧版默认信任 `project-neko.online`，若该域名被恶意改写解析地址可能绕过 Host 校验）。`NEKO_TRUSTED_HOSTS` / `NEKO_TRUSTED_ORIGINS` 默认留空，只在你拥有并核验的域名时显式配置，平时访问一律用 IP 字面量。

完整契约见 [社区账户与远程实例访问边界](../../docs/design/security/community-remote-access.md)。

启动后会自动完成：
- **`neko-init`**：一次性初始化，创建 `neko-home/`、`logs/` 并对齐到 UID/GID 1000，失败会阻止主服务启动。**仅在属主不匹配时才递归 `chown`**，避免每次启动对全目录做全量递归写（2G 机器 IO 宝贵）。
- **`neko-main`**：N.E.K.O 主服务（Compose 将等待 `neko-init` 成功后启动）。该服务通过 `extends` 继承官方 `../docker-compose.yml`（不再复制一份），此处只覆盖 2C2G 相关的差异，官方后续更新会自动带入。
- **`neko-cron-install`**：一次性把**自愈看门狗**装到宿主机 `/opt/neko/watchdog.sh`，并注册 `/etc/cron.d/neko-watchdog`，跑完即退出。

> 若需重新初始化：`docker compose -f "docker-compose.yaml" down && docker compose -f "docker-compose.yaml" up -d`

---

## 3. 服务与自愈机制

### 3.1 服务拓扑

```
neko-init ──(success)──▶ neko-main ──▶ 本机 48911(HTTP)/48912(HTTPS，可显式开放)
   │                        │
   └──(success)──▶ neko-cron-install ──▶ 宿主 /opt/neko/watchdog.sh + /etc/cron.d/neko-watchdog
                                    └──▶ 每 5 分钟二层健康检查 + 自动重启
```

### 3.2 自愈看门狗做了什么

由宿主 cron 每 5 分钟执行 `/opt/neko/watchdog.sh`，**双层健康判据** + 多重防误恢复机制：

- **身份与状态门槛**：核验容器 `neko` 的部署标签、Compose 服务名，且必须 `Running` **且未 `Paused`**。容器消失、手动停止、同名其他部署都不会被重启；进程退出交给 Docker 的 `unless-stopped` 策略。
- **启动宽限期（防止无限重启）**：2C2G 上 Python 启动很慢。看门狗读取容器 `StartedAt`，**每当容器（重新）启动，先清空失败计数，并在启动宽限期（默认 600 秒，可用 `/opt/neko/grace-seconds` 覆盖）内不判定失败、不重启**。因此慢启动不会被误判为"已死"而无限重启；`docker restart` 虽保留同一容器 ID，但 StartTime 变化会重置计数并重新进入宽限期。需要更长启动时间时提高 `grace-seconds`（例如 `1200`）。
- **健康探测（真实 HTTP，不用 `/dev/tcp`）**：宿主 `curl` 请求本机 48911 首页，完整响应为 200 或新版正常的匿名 401；同时 `docker exec` 在容器内直连真正主服务的 `/health`，要求请求成功。Nginx 的 `/health` 是静态 200，不能单独证明后端存活。两项探测均有总超时，收到状态码后仍超时也算失败。
- **连续 2 次不健康 → 自动重启同一个容器 ID**：重启前会再次确认该容器仍 `Running`、未 `Paused`、且 `StartedAt` 未变化——若你在此期间手动重启/暂停/停止，看门狗会**主动让位（stand down）**，不会和你争抢。主 `flock` 防止 cron 与手动调用同时执行；另用**独立的 `restart.lock`** 专门串行 `docker restart`，外部运维只要取同一把锁即可与看门狗协作，避免并发重启。
- **`paused` 视为存活**：容器被 `docker pause` 后会被当作存活处理——不探测、不计数、不重启，并清空失败计数。
- **重启超时不误报**：`timeout 60 docker restart` 偶发超时（退出码 124）只说明重启仍在慢速进行，**不算硬失败**——清空计数并让新的宽限期接管，避免"已重启又被判定失败"的双重重启。
- **暂停/维护让位**：容器被 `docker pause` 或设置了 `/opt/neko/disabled` 时，看门狗不恢复、不清算为故障。

计数绑定容器 ID，健康即清空失败计数；计数损坏或读写失败会报错退出，不会静默归零。安装器拒绝符号链接和非 root 私有目录，原子安装脚本与 cron。状态、锁、日志位于 root:root、0700 的 `/opt/neko/`；日志 `/opt/neko/watchdog.log` 无自动轮转，长期运行建议配置 logrotate。

开发者可运行 `sudo bash ./test-watchdog.sh` 验证恢复逻辑。测试将 Docker/HTTP 调用替换为模拟程序，安装路径改为临时目录，使用真实 Linux 权限、文件锁和计数读写；不安装真实 cron，也不重启容器。通过此测试不代表已完成 ECS 实机部署验收。

维护时先执行 `sudo touch /opt/neko/disabled` 暂停，再停容器；恢复运行后 `sudo rm -f /opt/neko/disabled`。重新安装不会解除暂停。安装器会写入宿主 root cron，只在信任这两个脚本和安装器镜像的主机上使用；多套部署不要共用 `neko` 容器名及 `/opt/neko`。

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
- 端口：`127.0.0.1:48911→80`（私有 HTTP）、`127.0.0.1:48912→443`（HTTPS；通过 `NEKO_HTTPS_BIND_IP` 显式开放）。不发布预留的 48915。
- **浏览器访问**：`https://<你的域名或IP>:48912`；公网仅放行 HTTPS 入口，可进一步限制来源 IP。默认本机绑定可用 SSH 转发连接。
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
- [ ] `neko-init`、`neko-cron-install` 一次性退出（`Exit 0`）
- [ ] 宿主机存在 `/opt/neko/watchdog.sh`（首行 `#!/bin/bash`）且 `+x`
- [ ] 宿主机存在 `/etc/cron.d/neko-watchdog`（权限 644、属主 root）
- [ ] 手动执行 `/opt/neko/watchdog.sh` 健康分支退出码为 0
- [ ] 镜像包含 #3289；HTTPS 首次输入实例凭证，刷新后可复用
- [ ] 公网 HTTP/私有 upstream 不可达，HTTPS 入口证书与白名单正确
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
sudo touch /opt/neko/disabled
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
