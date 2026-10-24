# 宿主机自愈看门狗（可选）

配合 `docker/docker-compose.yml` 使用的可选宿主机看门狗：Linux 宿主 cron 每 5 分钟检查一次 `neko` 容器，服务卡死时有限次数地自动 `docker restart`。它会写入 root cron，只在信任这些脚本的主机上安装。

| 文件 | 作用 |
|---|---|
| `watchdog.sh` | 安装到 `/opt/neko/watchdog.sh`，由 cron 执行的探测与恢复脚本 |
| `install-watchdog.sh` | 安装器：`sudo sh docker/watchdog/install-watchdog.sh --host` 直接在宿主机运行，不需要辅助镜像 |
| `test-watchdog.sh` | 隔离回归测试：`sudo bash docker/watchdog/test-watchdog.sh` |

前置条件、安装命令、维护和卸载步骤见[低配云服务器部署](../../docs/zh-CN/deployment/low-spec-server.md)第 5 节（[English](../../docs/deployment/low-spec-server.md)）。

## 多实例

看门狗跑在宿主 cron 里，读不到容器内的环境变量，所以每个实例都要在 cron 行上显式给出参数：

```cron
# 默认实例（容器名 neko）—— 保持不变
*/5 * * * * root /opt/neko/watchdog.sh

# 第二个实例（compose 里改了 NEKO_CONTAINER_NAME / NEKO_HTTP_PORT，且用了 -p neko-2）
*/5 * * * * root NEKO_WATCHDOG_CONTAINER=neko-2 NEKO_WATCHDOG_HTTP_PORT=48921 /opt/neko/watchdog.sh
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `NEKO_WATCHDOG_CONTAINER` | `neko` | 要盯着哪个容器（对应 compose 的 `NEKO_CONTAINER_NAME`） |
| `NEKO_WATCHDOG_HTTP_PORT` | `48911` | 宿主侧探测端口（对应 compose 的 `NEKO_HTTP_PORT`）。写错会去探默认实例：默认实例在跑时会把故障实例"探活"，没在跑时会把故障实例误判成持续失败并反复重启 |
| `NEKO_WATCHDOG_SERVICE` | `neko-main` | Compose 服务名。**一般不用改**：改 `container_name` 不会改服务名，容器上的 `com.docker.compose.service` 标签始终是 `neko-main` |
| `NEKO_WATCHDOG_STARTUP_GRACE_SECONDS` | `900` | 启动宽限期 |

状态文件按容器隔离：

| 容器 | 锁与状态文件 |
|---|---|
| `neko`（默认） | `/opt/neko/watchdog.lock`、`fail-count`、`restart-count` —— 沿用历史文件名，排障文档里的命令继续有效 |
| 其它容器 | 同上但带 `-<容器名>` 后缀，例如 `restart-count-neko-2` |

不隔离的话，两个 cron 任务会共用一份恢复计数：容器 ID 不匹配会把 `restart-count` 归零，两个容器交替故障时"最多自愈 3 次"的上限可能永远不生效；共用一把锁时，一个卡住的实例还会挡住另一个实例的探测。

`disabled` 保持全局：它是人工维护开关，不是恢复计数。
