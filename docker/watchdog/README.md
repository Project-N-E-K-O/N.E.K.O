# 宿主机自愈看门狗（可选）

配合 `docker/docker-compose.yml` 使用的可选宿主机看门狗：Linux 宿主 cron 每 5 分钟检查一次 `neko` 容器，服务卡死时有限次数地自动 `docker restart`。它会写入 root cron，只在信任这些脚本的主机上安装。

| 文件 | 作用 |
|---|---|
| `watchdog.sh` | 安装到 `/opt/neko/watchdog.sh`，由 cron 执行的探测与恢复脚本 |
| `install-watchdog.sh` | 在一次性 `alpine` 容器中运行的安装器 |
| `test-watchdog.sh` | 隔离回归测试：`sudo bash docker/watchdog/test-watchdog.sh` |

前置条件、安装命令、维护和卸载步骤见[低配云服务器部署](../../docs/zh-CN/deployment/low-spec-server.md)第 5 节（[English](../../docs/deployment/low-spec-server.md)）。
