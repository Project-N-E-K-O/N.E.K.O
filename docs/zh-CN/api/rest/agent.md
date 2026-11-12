# Agent API

**前缀：** `/api/agent`

这是主服务器面向浏览器的 Agent Server 环回代理。它负责角色/会话同步和远程部署安全检查，再把运行时操作转发到 `TOOL_SERVER_PORT`。代理失败通常返回 `502`；远程部署下修改类路由返回 `501`，因为控制服务器所在机器并不安全。

## 状态与命令

### `GET /api/agent/flags`

返回 Agent Server 的 flags 快照，包括总开关和三个能力开关：键鼠（`computer_use_enabled`）、浏览器（`browser_use_enabled`）、用户插件（`user_plugin_enabled`）。代理失败返回 `502` 和 `success: false`。

### `POST /api/agent/flags`

旧的部分更新入口。请求体为 `{"lanlan_name":"...","flags":{...}}`。路由会更新角色会话并转发已识别的子开关。角色不存在返回 `404`；转发失败会把本地 flags 重置为安全关闭状态并返回 `502`。

### `GET /api/agent/state`

返回 Agent Server 的权威状态快照：revision、flags、capabilities、notification 状态和在办的任务（`active_tasks`）。

### `POST /api/agent/command`

首选修改入口。当前命令如下：

| 命令 | 附加字段 | 用途 |
|---|---|---|
| `set_agent_enabled` | `enabled`，可选 `profile` | 切换总开关；关闭时会叫停全部在办的任务 |
| `set_flag` | `key`、`value` | 切换 `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` 之一 |
| `refresh_state` | 无 | 返回并广播新的状态快照 |

`request_id` 和 `lanlan_name` 可选。未知命令或 flag key 由 Agent Server 拒绝；上游/代理失败返回 `502`。

## 健康状态与能力

| 方法 | 路径 | 响应边界 |
|---|---|---|
| `GET` | `/api/agent/health` | `{"status":"ok","tool":{...}}`；Agent Server 不可用时返回 `502` 和 `status: "down"` |
| `GET` | `/api/agent/computer_use/availability` | Agent Server 返回的就绪状态与原因 |
| `GET` | `/api/agent/browser_use/availability` | 浏览器依赖/模型就绪状态 |
| `GET` | `/api/agent/user_plugin/availability` | 插件服务可达性；不可用时返回 `502` |

## 任务与管理

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/agent/tasks` | 列出任务：在办的，以及最近五分钟内结束的 |
| `GET` | `/api/agent/tasks/{task_id}` | 读取一个任务 |
| `POST` | `/api/agent/tasks/{task_id}/cancel` | 叫停一个任务；保留上游 `404`，请求已到达但响应超时时使用 `504`。任务已经结束时返回 `success: false` 和它的实际 `status` |
| `POST` | `/api/agent/admin/control` | 转发 `{"action":"end_all"}` 等管理操作；`end_all` 会叫停全部在办的任务。仅限本机 |

### 任务对象

一个任务就是猫爪台账里一件委托在 HUD 上的样子。`GET /api/agent/tasks` 返回 `{"tasks":[...]}`；状态快照的 `active_tasks` 和 WebSocket 的 `agent_task_update` 消息里是同一种对象。

| 字段 | 含义 |
|---|---|
| `id` | 委托号 |
| `status` | `queued`、`running`、`waiting_user`、`completed`、`blocked`、`failed`、`cancelled` 之一 |
| `type` | `user_plugin`（直达调用插件入口）、`task_agent`（task agent 在办，界面上叫「使魔」）或 `task`（刚登记，还没决定谁来办） |
| `start_time`、`end_time` | ISO 时间戳；任务结束后才有 `end_time` |
| `params` | `description`（标题）和 `instruction`（派发的那句话）；`user_plugin` 任务另有 `plugin_id`、`entry_id`、`plugin_name` |
| `lanlan_name` | 任务所属的角色 |
| `error` | 原因，`failed`、`blocked`、`cancelled` 的任务才有 |
| `progress`、`stage`、`message`、`step`、`step_total` | 直达调用的插件入口上报的进度，可选 |

`waiting_user` 表示任务在等用户回答或批准，仍算在办。`blocked` 表示这件事没办成就停下了，要先具备别的条件才能继续。以后可能增加字段。

## UI 辅助路由

| 方法 | 路径 | 边界 |
|---|---|---|
| `GET` | `/api/agent/user_plugin/dashboard` | 重定向到本地插件 dashboard；接受经校验的 `v` 和环回 `yui_opener_origin` 查询值 |

Dashboard 路由只是浏览器辅助入口，不是任务 API。浏览器没有提交任务的路由：任务由主对话模型用 `dispatch_task` 工具交办，主服务器再转给 Agent Server 的内部路由 `/nekoclaw/dispatch`。参见 [Agent Server API](/zh-CN/api/agent-server)。
