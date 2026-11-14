# Agent Server API

**默认地址：** `http://127.0.0.1:48915`

**配置项：** `TOOL_SERVER_PORT`

Agent Server 是内部环回服务，里面有猫爪宿主（任务大脑、task agent、能力网关）、给 HUD 用的任务视图、键鼠和浏览器两项内置能力，以及内嵌的插件服务。主服务器通过 `/api/agent` 暴露受支持的浏览器侧子集；本地进程组之外的调用方应使用该代理，而不是直接调用以下路由。

Agent Server 还通过 ZeroMQ 与主服务器异步交换会话和任务事件。因此 HTTP 是控制/查询面，ZeroMQ 是事件面；并不是“只使用 ZeroMQ 而不使用 HTTP”。

## HTTP 端点一览

| 方法 | 路径 | 契约 |
|---|---|---|
| `GET` | `/health` | N.E.K.O 健康指纹及当前 agent flags |
| `GET` | `/agent/flags` | 当前总开关和子功能开关 |
| `POST` | `/agent/flags` | 部分更新 `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` |
| `GET` | `/agent/state` | 权威 revision、flags、capabilities、notification 和在办的任务 |
| `POST` | `/agent/command` | `set_agent_enabled`、`set_flag` 或 `refresh_state` 命令 |
| `GET` | `/computer_use/availability` | 键鼠能力的就绪状态及原因 |
| `GET` | `/browser_use/availability` | 浏览器能力的依赖和模型就绪状态 |
| `POST` | `/nekoclaw/dispatch` | 把主对话的一句话交给任务大脑，返回第一轮的回执 |
| `POST` | `/nekoclaw/query` | 问在办什么、某件事能不能办；不调模型 |
| `GET` | `/nekoclaw/brief` | 在办的任务，每件一行，供新对话会话的开场提示使用 |
| `GET` | `/tasks` | 列出在办的任务和最近五分钟内结束的任务 |
| `GET` | `/tasks/{task_id}` | 读取一个任务；不存在时返回 `404` |
| `POST` | `/tasks/{task_id}/cancel` | 叫停一个任务；不存在时返回 `404`，已经结束的返回 `success: false` 和它的 `status` |
| `POST` | `/api/agent/tasks/{task_id}/complete` | 插件 deferred 结果的完成回调 |
| `POST` | `/admin/control` | 管理操作；`action: "end_all"` 会叫停全部在办的任务 |
| `POST` | `/notify_config_changed` | 模型/API 配置修改后的内部通知 |

`/tasks` 返回的任务对象见 [Agent API](/zh-CN/api/rest/agent) 的「任务对象」一节，以后可能增加字段。

`/api/agent/tasks/{task_id}/complete` 和 `/notify_config_changed` 属于进程内部。不得把 `/admin/control` 和 `/nekoclaw/*` 路由暴露到不可信网络。

## 猫爪路由

这三个路由由同一台机器上的主服务器调用，替主对话模型办事；不代理给浏览器。

### `POST /nekoclaw/dispatch`

请求体：`{"text":"...","lanlan_name":"...","user_text":"...","conversation_id":"...","language":"..."}`。`text` 是模型传给 `dispatch_task` 工具的那句话，`lanlan_name` 是角色名，两者必填（否则 `400`）。`user_text` 是这一轮的用户原话，插件入口会在 `_ctx.latest_user_request` 里收到它。

响应是 `{"success":true,"kind":"...","reason":"...","text":"...","commission_id":"...","model_calls":0}`。`text` 是给模型看的回执。`kind` 是 `accepted`（受理）、`received`（第一轮超过 1 秒，结论随后作为汇报送达）、`duplicate`、`no_capability`、`stopped`、`told`、`answered`、`ambiguous`、`nothing_active`、`unclear_answer` 之一。`kind` 为 `no_capability` 时，`reason` 是 `disabled`、`not_installed`、`not_enabled`、`denied`、`no_match` 之一。这个调用只等第一轮；事情本身在后台继续办。

### `POST /nekoclaw/query`

请求体：`{"text":"...","lanlan_name":"...","language":"..."}`，`lanlan_name` 必填。响应是 `{"success":true,"text":"...","can_do":true|false|null}`。问的是进展，或者不调模型就答不了时，`can_do` 为 `null`。

### `GET /nekoclaw/brief`

查询参数：`lanlan_name`，可选 `language`。返回 `{"success":true,"lines":[...]}`：该角色名下排队中、在办、等用户回话的委托，每件一行。总开关关闭时列表为空。

## ZeroMQ 事件面

地址只绑定环回，可用对应的 `NEKO_ZMQ_*_PORT` 环境变量覆盖。

| Socket | 默认地址 | 类型 | 方向 |
|---|---|---|---|
| 会话事件 | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| 任务与状态事件 | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| 可靠的事后检查队列 | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

事件包括会话生命周期/意图恢复信号、事后检查请求及其确认、任务更新、任务结果、主动消息和 agent 状态快照。关于委托的汇报先写进宿主的 outbox 再发送，桥接没收下就一直重试。事件 payload 是随主进程和 agent 进程共同版本化的内部格式，不是公开插件协议。

## 谁在干活

| 部分 | 当前职责 |
|---|---|
| 插件入口 | 一个入口就能办完的事，由任务大脑经插件服务的 `/runs` 协议直达调用 |
| task agent | 办多步任务的执行会话（界面上叫「使魔」），跑在内置的 agent runtime（`nekoclaw/runtime/`）里 |
| 能力网关 | 回环地址上的 MCP 服务，task agent 经它调用插件入口、MCP 工具和两项内置能力 |
| 键鼠、浏览器 | 内置能力，分别由 `brain/computer_use.py` 和可选的 `browser-use` 依赖实现；只有 task agent 能调用 |

MCP 服务在 `plugin/plugins/mcp_adapter/` 下的 `mcp_adapter` 插件里配置，网关把它们的工具提供给 task agent。

架构参见 [Agent 系统](/zh-CN/architecture/agent-system)，主服务器代理参见 [Agent API](/zh-CN/api/rest/agent)。
