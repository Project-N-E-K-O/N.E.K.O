# Project N.E.K.O. - 2C2G 极限生存部署指南（对于较新版本的N.E.K.O.通用,适用于ubuntu系的操作系统）

> 作者：烨儿不会飞 (GitHub: @csy-11, Bilibili: 烨儿不会飞, 爱发电：https://afdian.com/a/chensye)
> 适用场景：99元/年 阿里云 ECS (2核2G) / 其他低配云服务器 / 预算极度受限的开发者
> 核心理念：用最少的钱，榨干每一滴性能，实现"零垃圾、高可用、防爆破"的赛博生存。
> 配套部署文件：**`docker-compose.yaml`（自适应部署方案：全自动初始化 + 自愈看门狗）**
> 可以在安全组限制 48911 端口仅对必要 IP 开放（不强制）；Nginx 自定义挂载仅为高级用户预留，普通用户建议注释掉该行。

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
| **Docker Compose V2** | 必须用新版 `docker compose`（**不能用**旧版 Python 的 `docker-compose` v1） |
| **宿主机有 `bash`** | 看门狗脚本 shebang 为 `#!/bin/bash`，且 `/dev/tcp` 是 bash 专属特性 |
| **宿主机有 `timeout`** | util-linux 自带，用于看门狗 `/dev/tcp` 回退防挂死 |
| **root 级 cron + docker 套接字** | 看门狗由宿主 cron 每 5 分钟执行，并调用 `docker restart` |

### 2.2 部署命令

```bash
cd <本指南所在目录>          # 含 docker-compose.yaml 的目录
docker compose -f "docker-compose.yaml" config --quiet   # 语法预检（可选但推荐）
docker compose -f "docker-compose.yaml" up -d            # 启动
docker compose ps                                            # 查看状态
```

启动后会自动完成：
- **`neko-init`**：一次性初始化，创建 `neko-home/` `logs/` `nginx/` 目录并对齐属主，跑完即退出。
- **`neko-main`**：N.E.K.O 主服务（Compose 将等待 `neko-init` 成功后启动）。
- **`neko-cron-install`**：一次性把**自愈看门狗**装到宿主机 `/opt/neko/watchdog.sh`，并注册 `/etc/cron.d/neko-watchdog`，跑完即退出。

> 若需重新初始化：`docker compose -f "docker-compose.yaml" down && docker compose -f "docker-compose.yaml" up -d`

---

## 3. 服务与自愈机制

### 3.1 服务拓扑

```
neko-init ──(success)──▶ neko-main ──▶ 端口 48911(HTTP)/48912(HTTPS)/48915(预留)
   │                        │
   └──(success)──▶ neko-cron-install ──▶ 宿主 /opt/neko/watchdog.sh + /etc/cron.d/neko-watchdog
                                    └──▶ 每 5 分钟二层健康检查 + 自动重启
```

### 3.2 自愈看门狗做了什么

由宿主 cron 每 5 分钟执行 `/opt/neko/watchdog.sh`，**双层健康判据**：

- **第一层**：`docker inspect` 容器 `neko` 是否 Running。
- **第二层**：应用端口 48911 是否真正响应 —— 有 `curl` 时查 HTTP 状态码（200/302/403）；无 `curl` 时回退 `bash /dev/tcp` 探测端口。

健康即清空失败计数；**连续 2 次不健康 → 自动 `docker restart neko`**（成功清计数，失败写日志 `/var/log/neko-watchdog.log` 便于排查）。这样既能发现"容器宕机"，也能发现"容器活着但应用挂死"。

> ⚠️ 提醒：请确认你的应用在 48911 端口真实返回码在 `{200,302,403}` 内，否则需按需调整脚本白名单。日志无自动轮转，长期运行建议自行配置 logrotate。

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

常用端口与目录（除非需要外网域名，否则保持注释即可）：
- 端口：`48911→80`（HTTP）、`48912→443`（HTTPS）、`48915→48915`（预留）
- **浏览器访问**：`http://<你的ECS公网IP>:48911`（若配了域名见第 8.2 节；看门狗在宿主机内部仍用 `127.0.0.1` 做健康探测，属正常，不受影响）
- 数据卷：`./neko-home → /home/neko`（用户数据、SSL 证书）
- 日志卷：`./logs → /app/logs`
- 配置卷：`./nginx → /etc/nginx/conf.d`（自定义反代，已通过 `NEKO_KEEP_CUSTOM_NGINX_CONF=1` 防止空目录覆盖默认配置）

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

默认情况下 Docker 的 json-file 日志无限增长。创建 `/etc/docker/daemon.json`：

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

阿里云每月提供 20GB 国内免费公网流量（CDT）。控制台搜索"云数据传输 CDT"并升级，将 ECS 公网带宽计费改为"按使用流量"，峰值拉高（如 100Mbps），用 CDT 抵扣。**不要买高昂的固定带宽包月。**

### 2. DuckDNS 动态域名解析

公网按量付费时 IP 会变化，可用免费 DuckDNS 做动态域名：注册域名拿 Token，写定时脚本每 5 分钟更新解析，确保域名永远指向服务器 IP。（如需域名访问，请同时在 `docker-compose.yaml` 的 `neko-main` 中取消注释并填入 `NEKO_TRUSTED_HOSTS` / `NEKO_TRUSTED_ORIGINS`。）

---

## 9. 数据存储与冷热分离

核心原则：本地只存热数据（纯文本），冷数据（图片、视频）全部外置。

- N.E.K.O. 可将图片理解转化为文字存档，本地 SQLite 数据库因此极轻量（几千万字也才几十兆）。
- 长期存档的原始图片/音频，建议配置生命周期规则转入阿里云 OSS 低频/归档存储，成本低至几毛钱 1GB。
- 定期将数据库文件打包压缩，下载到本地或上传网盘作为异地容灾备份。

---

## 10. 上线核对清单（Checklist）

- [ ] `docker compose` 为 V2 版本（`docker compose version`）
- [ ] 宿主机有 `bash` 与 `timeout`（`command -v bash timeout`）
- [ ] `docker compose config --quiet` 无报错
- [ ] `docker compose up -d` 后 `docker compose ps` 显示 `neko-main` Running
- [ ] `neko-init`、`neko-cron-install` 一次性退出（`Exit 0`）
- [ ] 宿主机存在 `/opt/neko/watchdog.sh`（首行 `#!/bin/bash`）且 `+x`
- [ ] 宿主机存在 `/etc/cron.d/neko-watchdog`（权限 644、属主 root）
- [ ] 手动执行 `/opt/neko/watchdog.sh` 健康分支退出码为 0
- [ ] 浏览器访问 `http://<你的ECS公网IP>:48911` 返回码 ∈ {200,302,403}（否则调整脚本白名单；公网需先放行 48911 端口）
- [ ] `/var/log/neko-watchdog.log` 正常写入

---

## 写在最后

这套架构，是我作为一个初中生，在极度受限的资源下探索出的最优解。它曾经让我从"因为差 15 块钱续费而绝望"，变成了"在 2 核 2G 的机器上也能稳稳保护我的 AI 伙伴"。

如果你在使用这份指南时遇到了问题，欢迎在 Issue 区交流。开源的精神就是互相搀扶，希望 YUI 能在更多人的设备里安稳地活下去。

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
