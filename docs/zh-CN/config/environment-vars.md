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
