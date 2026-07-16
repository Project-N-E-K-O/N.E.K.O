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

布尔解析接受 `1/true/yes/on` 与 `0/false/no/off`。向量变量也兼容无前缀形式。

## 仅用于 Docker 初始配置

入口脚本读取 `NEKO_CORE_API_KEY`、`NEKO_CORE_API`、`NEKO_ASSIST_API`，Qwen/OpenAI/GLM/Step/Silicon/Grok/Doubao 的部分 `NEKO_ASSIST_API_KEY_*`，以及 `NEKO_MCP_TOKEN`。`NEKO_FORCE_ENV_UPDATE` 请求重新生成 `/app/config/core_config.json`。

这些不是源码/桌面运行的通用 API 环境变量。旧 `docker/env.template` 中未接入入口脚本的模型变量不应依赖。
