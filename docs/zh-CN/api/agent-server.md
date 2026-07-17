# Agent Server API

**默认地址：** `http://127.0.0.1:48915`

**配置项：** `TOOL_SERVER_PORT`

Agent Server 是内部环回服务，负责 agent 运行时、受跟踪任务、执行适配器和插件直接执行。主服务器通过 `/api/agent` 暴露受支持的浏览器侧子集；本地进程组之外的调用方应使用该代理，而不是直接调用以下路由。

Agent Server 还通过 ZeroMQ 与主服务器异步交换会话和任务事件。因此 HTTP 是控制/查询面，ZeroMQ 是事件面；并不是“只使用 ZeroMQ 而不使用 HTTP”。

## HTTP 端点一览

| 方法 | 路径 | 契约 |
|---|---|---|
| `GET` | `/health` | N.E.K.O 健康指纹及当前 agent flags |
| `GET` | `/capabilities` | 当前 capability 快照 |
| `GET` | `/agent/flags` | 当前主开关和子功能开关 |
| `POST` | `/agent/flags` | 部分更新子功能开关 |
| `GET` | `/agent/state` | 权威 revision、flags、capabilities、notification 和任务状态 |
| `POST` | `/agent/command` | `set_agent_enabled`、`set_flag` 或 `refresh_state` 命令 |
| `GET` | `/computer_use/availability` | Computer Use 就绪状态及原因 |
| `POST` | `/computer_use/run` | 启动受跟踪的 Computer Use 任务；请求体必填 `instruction`，可选 `screenshot_b64`、`lanlan_name` |
| `GET` | `/browser_use/availability` | Browser Use 依赖和模型就绪状态 |
| `POST` | `/browser_use/run` | 执行一条浏览器指令；请求体必填 `instruction` |
| `GET` | `/openclaw/availability` | OpenClaw/QwenPaw 能力检查 |
| `GET` | `/openfang/availability` | OpenFang 能力检查 |
| `POST` | `/openfang/run` | 启动受跟踪的 OpenFang 任务 |
| `POST` | `/openfang/sync_config` | 刷新 OpenFang 运行时配置 |
| `GET` | `/mcp/availability` | 兼容响应：MCP 已从 `brain/` 移除，因此这里始终不可用 |
| `POST` | `/plugin/execute` | 直接调度用户插件 entry；必填 `plugin_id`，可选 `entry_id`、`args`、角色和会话 ID |
| `GET` | `/tasks` | 列出任务注册表快照 |
| `GET` | `/tasks/{task_id}` | 读取单个受跟踪任务；不存在时返回 `404` |
| `POST` | `/tasks/{task_id}/cancel` | 取消受跟踪任务；不存在时返回 `404` |
| `POST` | `/api/agent/tasks/{task_id}/correction` | 任务结果的内部修正回调 |
| `POST` | `/api/agent/tasks/{task_id}/complete` | 任务结果的内部完成回调 |
| `POST` | `/admin/control` | 管理运行时；当前 `action: "end_all"` 会取消活动任务 |
| `POST` | `/notify_config_changed` | 模型/API 配置修改后的内部通知 |

多数 run 端点返回 `{"success":true,"task_id":"...","status":"running","start_time":"..."}`，随后异步继续。参数错误为 `400`；主开关或功能关闭为 `403`；适配器不可用为 `503`；Computer Use 检测到重复任务时为 `409`。任务和插件结果属于内部 schema，未来可能增加字段。

两个 `/api/agent/tasks/*` 回调和 `/notify_config_changed` 属于进程内部。不得把 `/admin/control`、直接 run 路由或 `/plugin/execute` 暴露到不可信网络。

## ZeroMQ 事件面

地址只绑定环回，可用对应的 `NEKO_ZMQ_*_PORT` 环境变量覆盖。

| Socket | 默认地址 | 类型 | 方向 |
|---|---|---|---|
| 会话事件 | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| 任务与状态事件 | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| 可靠分析队列 | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

事件包括会话生命周期/意图恢复信号、分析请求、任务更新、任务结果、主动消息和 agent 状态快照。事件 payload 是随主进程和 agent 进程共同版本化的内部格式，不是公开插件协议。

## 执行适配器

| 适配器 | 当前职责 |
|---|---|
| Computer Use | 基于截图的鼠标和键盘执行 |
| Browser Use | 通过可选 `browser-use` 依赖执行浏览器自动化 |
| OpenClaw | 委派到 OpenClaw/QwenPaw 独立 agent 渠道 |
| OpenFang | 委派到 OpenFang 独立 agent 渠道 |
| 用户插件 | 通过插件运行时直接执行 |

MCP 调用已不在 `brain/` 中实现；可安装的 MCP 集成位于 `plugin/plugins/mcp_adapter/`。

架构参见 [Agent 系统](/zh-CN/architecture/agent-system)，主服务器代理参见 [Agent API](/zh-CN/api/rest/agent)。
