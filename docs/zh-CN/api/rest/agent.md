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
| `GET` | `/api/agent/computer_use/availability` | Agent Server 返回的键鼠就绪状态与原因：agent 模型连得上，并且本机能发出键鼠输入 |
| `GET` | `/api/agent/browser_use/availability` | 浏览器就绪状态与原因：本机装有可用的浏览器，并且 agent 模型连得上。找不到浏览器时原因码是 `AGENT_BROWSER_NOT_FOUND` |
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

## 键鼠与浏览器工具

键鼠操控和浏览器操控是两组内置工具，由 task agent（界面上叫「使魔」）一步一步调用，参见 [Agent 系统](/zh-CN/architecture/agent-system)。浏览器操控用的是本机已安装的 Chromium 内核浏览器（Chrome、Edge、Brave、Chromium）。应用不随包附带浏览器，启动时也不下载。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/agent/builtin/activity` | 现在是哪个任务在用键鼠、哪个在用浏览器 |
| `POST` | `/api/agent/builtin/stop` | 叫停正在用键鼠或浏览器的任务 |
| `GET` | `/api/agent/builtin/browser/settings` | 浏览器工具用哪个浏览器、怎么用，以及本机找到的浏览器 |
| `POST` | `/api/agent/builtin/browser/settings` | 修改上述设置 |

远程部署下这四个路由都返回 `501`。

### `GET /api/agent/builtin/activity`

返回 `{"activity":[...]}`：正在被使用的每一组一项，两组都没在用时是空列表。

```json
{"group": "browser", "state": "started", "commission_id": "...", "window": null}
```

| 字段 | 含义 |
|---|---|
| `group` | `keyboard_mouse`（整个桌面）或 `browser` |
| `state` | 本接口里固定是 `started`；下面的 WebSocket 消息里是 `started` 或 `ended` |
| `commission_id` | 占着这一组的任务，和任务对象的 `id` 是同一个值 |
| `window` | 浏览器这一组：被操作的窗口确定后是 `{"browser","pid","window_id","bounds"}`，否则为 `null`。键鼠这一组始终为 `null` |

代理失败返回 `502`，`activity` 为空列表，并带 `error`。

任务开始使用、停止使用某一组时，主服务器会把同一种对象作为 WebSocket 消息 `{"type":"agent_builtin_activity","activity":{...}}` 发给每个已连接的页面。同一次使用可能收到不止一条 `started`：浏览器是任务第一次调用时才启动的，所以窗口第一次确定、以及窗口位置变化时会再发一条。`ended` 在这些时候发出：task agent 这一轮跑完、任务结束或失败、任务进入等用户回话、任务被叫停。页面把最新的对象存在 `window._agentBuiltinActivity[group]`，并派发 `neko-agent-builtin-activity` DOM 事件（`detail` 就是这个对象），自己不画任何东西。

### `POST /api/agent/builtin/stop`

请求体：可选的 `{"group":"keyboard_mouse"}` 或 `{"group":"browser"}`；不带 `group` 就两组都停。响应是 `{"success":true,"stopped":["<任务 id>", ...]}`，没有任务在用时列表为空。它做的事和对占着这一组的任务调用 `POST /api/agent/tasks/{task_id}/cancel` 一样。未知的 group 返回 `400`；`504` 表示请求已到达 Agent Server，但响应超时。

### `GET /api/agent/builtin/browser/settings`

```json
{
  "settings": {"mode": "own_profile", "executable": null, "headless": false},
  "modes": ["own_profile", "attach"],
  "browsers": [{"kind": "chrome", "name": "Google Chrome", "executable": "..."}]
}
```

`browsers` 是本机找到的浏览器，优先使用的排在最前；`kind` 是 `chrome`、`edge`、`brave`、`chromium` 或 `custom`。

| 设置项 | 含义 |
|---|---|
| `mode` | `own_profile`（默认）：启动用户的浏览器，配一份放在数据根下的独立资料目录（`nekoclaw/browser_profile`），要用的网站在里面登录一次即可。`attach`：直接操控用户正在用的浏览器；要求 Chrome 144 以上，用户自己在 `chrome://inspect/#remote-debugging` 打开远程调试，每次连接都要在浏览器里点允许 |
| `executable` | 指定要用的浏览器路径，代替自动找到的那个；`null` 表示自动查找 |
| `headless` | 启动浏览器时不开窗口；只对 `own_profile` 有效 |

### `POST /api/agent/builtin/browser/settings`

请求体：`mode`、`executable`、`headless` 中的任意几项，没带的字段保持原值。响应是上面的对象再加 `"success":true`。`mode` 未知，或 `executable` 指向的文件不存在，返回 `400`。改动从下一次启动浏览器起生效：浏览器工具自己启动的浏览器会被关掉。

## UI 辅助路由

| 方法 | 路径 | 边界 |
|---|---|---|
| `GET` | `/api/agent/user_plugin/dashboard` | 重定向到本地插件 dashboard；接受经校验的 `v` 和环回 `yui_opener_origin` 查询值 |

Dashboard 路由只是浏览器辅助入口，不是任务 API。浏览器没有提交任务的路由：任务由主对话模型用 `dispatch_task` 工具交办，主服务器再转给 Agent Server 的内部路由 `/nekoclaw/dispatch`。参见 [Agent Server API](/zh-CN/api/agent-server)。
