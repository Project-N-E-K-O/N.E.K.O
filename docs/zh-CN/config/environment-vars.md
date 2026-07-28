# 环境变量

只支持当前代码明确读取的变量。运行时变量优先使用 `NEKO_` 前缀；部分网络配置兼容无前缀名称。

## 端口

| 变量 | 默认值 | 服务 |
| --- | ---: | --- |
| `NEKO_MAIN_SERVER_PORT` | 48911 | 主 Web/API |
| `NEKO_MEMORY_SERVER_PORT` | 48912 | 记忆服务 |
| `NEKO_MONITOR_SERVER_PORT` | 48913 | 监控服务 |
| `NEKO_COMMENTER_SERVER_PORT` | 48914 | 评论服务 |
| `NEKO_TOOL_SERVER_PORT` | 48915 | Agent/工具服务 |
| `NEKO_USER_PLUGIN_SERVER_PORT` | 48916 | 用户插件宿主 |
| `NEKO_AGENT_MQ_PORT` | 48917 | Agent 消息传输 |
| `NEKO_MAIN_AGENT_EVENT_PORT` | 48918 | 主服务/Agent 事件传输 |
| `NEKO_OPENFANG_PORT` | 50051 | OpenFang A2A |

Electron 的 `port_config.json` 位于平台配置目录；显式环境变量优先。

## 运行时、存储与向量

`NEKO_INSTANCE_ID`、`NEKO_AUTOSTART_CSRF_TOKEN`、`NEKO_AUTOSTART_ALLOWED_ORIGINS`、`NEKO_BEHIND_PROXY`、`NEKO_LOG_LEVEL`、`NEKO_MERGED` 用于运行时。存储根由 launcher 通过 `NEKO_STORAGE_SELECTED_ROOT` 和 `NEKO_STORAGE_ANCHOR_ROOT` 传入。

本地向量使用：

- `NEKO_VECTORS_ENABLED`：默认开启；
- `NEKO_VECTORS_QUANTIZATION`：`auto`、`int8` 或 `fp32`；

可用内存门槛目前是固定的运行时常量 `VECTORS_MIN_RAM_GB = 4.0`，没有对应的环境变量覆盖项。

## 进程模型与单实例

launcher 是前台进程：绝不守护化脱管，属主进程一消失就把整套服务拓扑拆掉；同时用
操作系统文件锁自证唯一，并在锁旁边写出权威运行时记录（pid、实例 ID、协商后的端口）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_OWNER_PID` | 本进程的父进程 | 父死守卫要盯的 pid。属主**不是**直接父进程时才需要设置——例如存储迁移交接产生的下一代 launcher，它的 spawn 者是故意退出的。 若属主打算靠读取 `launcher.json` 来认领运行时，应当设置本变量：它会成为记录里的 `owner_pid`，那才是该比对的字段。不要比对 `parent_pid`——Windows 开发态下 `Popen(sys.executable)` 启动的是一个再拉起真解释器的壳，`parent_pid` 指的是那个壳而不是属主（CI 实测；macOS 与 Linux 直接匹配，打包态没有这个壳）。 |
| `NEKO_OWNER_RELAUNCH` | 未设置 | `1` 表示属主会自己负责重启运行时。此时存储迁移重启只干净退出、等待属主拉起，不再自旋出下一代。 Windows 上强烈建议设置：不设时 launcher 会自旋下一代，为了不连带杀死替身必须解除旧 Job 的管理，于是任何活过 cleanup 的进程（插件、MCP、Chromium）都不会被回收。 |
| `NEKO_PARENT_DEATH_GUARD` | `1` | 设为 `0` 完全关闭父死守卫。仅用于会重挂父进程的调试器/性能分析工具；关掉之后运行时可能活得比属主久。 |
| `NEKO_LAUNCHER_RESTART_HANDOFF` | 未设置 | 由上一代 launcher 设在下一代身上，让它等待单实例锁释放，而不是判定"已有实例在跑"。不需要手工设置。 |
| `NEKO_RUNTIME_STATE_DIR` | 按用户的运行时目录 | 覆盖 `launcher.lock` 与 `launcher.json` 的位置。默认 Windows `%LOCALAPPDATA%\N.E.K.O\runtime`、macOS `~/Library/Application Support/N.E.K.O/runtime`、Linux `~/.local/state/N.E.K.O/runtime`。Linux 路径刻意不看 `XDG_RUNTIME_DIR`：该变量在桌面会话里有、在 cron / 裸 SSH / `su` / system unit / 多数容器里没有，据它推导锁路径会让同一个用户持有两把不同的锁、同时跑起两个运行时。覆盖值会被原样使用、不追加用户后缀，所以必须指向当前用户私有的目录。POSIX 上该目录仍会被校验：属于其他 uid 的目录（或指向它的软链）以 EPERM 拒绝，带 group/world 权限位的目录会被就地 chmod 成 0700；Windows 上两项都不做。被拒绝时按 unknown 处理——launcher 会带告警继续启动，但没有唯一性证明。指到多用户共享目录会破坏单实例证明：Windows 上两个用户会争同一把锁，POSIX 上第二个用户打不开第一个用户的锁文件，会在没有唯一性证明的情况下启动。 |

## 运行拓扑

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_MERGED` | 源码环境：`0`；冻结发行包：`1` | `1` 让 main、memory、agent 三个 HTTP 服务在同一进程中运行，但保留原有契约；`0` 保留三个服务进程。不会复用不完整或混合的已有后端；即使原本选择 merged，也会在隔离的回退端口上强制启动三个服务进程。 |

源码开发、独立服务监管或需要 agent 故障隔离时应使用多进程模式。发行包可通过
`NEKO_MERGED=0` 立即回退。

通用布尔解析通常接受 `1/true/yes/on` 与 `0/false/no/off`；`NEKO_MERGED` 自身只接受 `1/true/yes` 与 `0/false/no`。向量变量也兼容无前缀形式。

## 仅用于 Docker 初始配置

入口脚本读取 `NEKO_CORE_API_KEY`、`NEKO_CORE_API`、`NEKO_ASSIST_API`，Qwen/OpenAI/GLM/Step/Silicon/Grok/Doubao 的部分 `NEKO_ASSIST_API_KEY_*`，以及 `NEKO_MCP_TOKEN`。`NEKO_FORCE_ENV_UPDATE` 请求重新生成 `/app/config/core_config.json`。

这些不是源码/桌面运行的通用 API 环境变量。旧 `docker/env.template` 中未接入入口脚本的模型变量不应依赖。
