# Agent Server API

**默认地址：** `http://127.0.0.1:48915`

**配置项：** `TOOL_SERVER_PORT`

Agent Server 是内部环回服务，里面有猫爪宿主（任务大脑、task agent、能力网关）、给 HUD 用的任务视图、键鼠和浏览器两组内置工具，以及内嵌的插件服务。主服务器通过 `/api/agent` 暴露受支持的浏览器侧子集；本地进程组之外的调用方应使用该代理，而不是直接调用以下路由。

Agent Server 还通过 ZeroMQ 与主服务器异步交换会话和任务事件。因此 HTTP 是控制/查询面，ZeroMQ 是事件面；并不是“只使用 ZeroMQ 而不使用 HTTP”。

## HTTP 端点一览

| 方法 | 路径 | 契约 |
|---|---|---|
| `GET` | `/health` | N.E.K.O 健康指纹及当前 agent flags |
| `GET` | `/agent/flags` | 当前总开关和子功能开关 |
| `POST` | `/agent/flags` | 部分更新 `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` |
| `GET` | `/agent/state` | 权威 revision、flags、capabilities、notification 和在办的任务 |
| `POST` | `/agent/command` | `set_agent_enabled`、`set_flag` 或 `refresh_state` 命令 |
| `GET` | `/computer_use/availability` | 键鼠的就绪状态及原因 |
| `GET` | `/browser_use/availability` | 浏览器的就绪状态及原因 |
| `GET` | `/builtin/activity` | 现在是哪件委托占着键鼠、哪件占着浏览器 |
| `POST` | `/builtin/stop` | 叫停占着键鼠或浏览器的那件委托 |
| `GET` | `/builtin/browser/settings` | 浏览器工具用哪个浏览器、怎么用，以及本机找到的浏览器 |
| `POST` | `/builtin/browser/settings` | 修改上述设置 |
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

## 内置工具路由

这些路由和 task agent 调用的键鼠工具、浏览器工具有关。主服务器在 `/api/agent` 下代理了全部这些路由。

### `GET /computer_use/availability` 与 `GET /browser_use/availability`

两者都返回 `{"enabled":true,"ready":true|false,"reasons":[...],"provider":"nekoclaw"}`，键鼠的响应另带 `model`。agent 模型还没配置好时，只返回 `{"ready":false,"reasons":[...]}`。

- 键鼠就绪的条件：agent 模型的连通性探测通过，并且本机能发出键鼠输入。发不了输入时，`reasons` 里是某个 `AGENT_PYAUTOGUI_*` 原因码，例如没有显示环境的机器上是 `AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE`。
- 浏览器就绪的条件：本机装有可用的浏览器，并且 agent 模型的连通性探测通过。应用不自带浏览器；找不到 Chrome、Edge、Brave、Chromium 中的任何一个时，`reasons` 是 `["AGENT_BROWSER_NOT_FOUND"]`。查找只是几次文件检查，结果缓存 30 秒。

### `GET /builtin/activity`

返回 `{"activity":[...]}`：现在被委托占用的每一组一项，两组都没人用时是空列表。每项是 `{"group":"keyboard_mouse"|"browser","state":"started","commission_id":"...","window":null}`。浏览器这一组在被操作的窗口确定之后，`window` 是 `{"browser":"...","pid":...,"window_id":...,"bounds":{...}}`；键鼠这一组指的是整个桌面，`window` 始终为 `null`。每项的结构和 `builtin_activity` 事件相同，供启动较晚、没收到事件的显示方补上现状。

### `POST /builtin/stop`

请求体：可选的 `{"group":"keyboard_mouse"}` 或 `{"group":"browser"}`；不带 `group` 就两组都停。未知的 group 返回 `400`。响应是 `{"success":true,"stopped":["<委托号>", ...]}`，没有委托占用时列表为空。叫停和对这件委托调用 `POST /tasks/{task_id}/cancel` 做的事一样：先写状态，再取消正在做的调用，桌面或浏览器随即放开。

### `GET /builtin/browser/settings`

返回 `{"settings":{"mode":"own_profile","executable":null,"headless":false},"modes":["own_profile","attach"],"browsers":[{"kind":"chrome","name":"Google Chrome","executable":"..."}]}`。`settings` 是 `<数据根>/nekoclaw/browser.json` 的内容，文件不存在时是默认值。`browsers` 是本机找到的浏览器，优先使用的排在最前。

| 字段 | 含义 |
|---|---|
| `mode` | `own_profile`（默认）：用用户的浏览器，配一份独立的资料目录 `<数据根>/nekoclaw/browser_profile`。`attach`：直接操控用户正在用的浏览器，要求 Chrome 144 以上，并在 `chrome://inspect/#remote-debugging` 打开远程调试 |
| `executable` | 指定要用的浏览器路径，代替自动找到的那个；`null` 表示自动查找 |
| `headless` | 启动浏览器时不开窗口；只对 `own_profile` 有效 |

### `POST /builtin/browser/settings`

请求体：`mode`、`executable`、`headless` 中的任意几项，没带的字段保持原值。`mode` 未知，或 `executable` 指向的文件不存在，返回 `400`。响应和 `GET` 相同，另加 `"success":true`。浏览器工具自己启动的浏览器会被关掉，下一次调用按新设置重新启动。

## ZeroMQ 事件面

地址只绑定环回，可用对应的 `NEKO_ZMQ_*_PORT` 环境变量覆盖。

| Socket | 默认地址 | 类型 | 方向 |
|---|---|---|---|
| 会话事件 | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| 任务与状态事件 | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| 可靠的事后检查队列 | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

事件包括会话生命周期/意图恢复信号、事后检查请求及其确认、任务更新、任务结果、主动消息、agent 状态快照，以及 `builtin_activity`（某件委托开始或停止占用键鼠、浏览器）。关于委托的汇报先写进宿主的 outbox 再发送，桥接没收下就一直重试。事件 payload 是随主进程和 agent 进程共同版本化的内部格式，不是公开插件协议。

## 谁在干活

| 部分 | 当前职责 |
|---|---|
| 插件入口 | 一个入口就能办完的事，由任务大脑经插件服务的 `/runs` 协议直达调用 |
| task agent | 办多步任务的执行会话（界面上叫「使魔」），跑在内置的 agent runtime（`nekoclaw/runtime/`）里 |
| 能力网关 | 回环地址上的 MCP 服务，task agent 经它调用插件入口、MCP 工具和内置工具 |
| 键鼠、浏览器 | 两组内置工具（`nekoclaw/builtin/`），task agent 在自己的循环里一步一步调用：键鼠是一个 `computer` 工具，浏览器是 15 个 `browser_*` 工具，经调试协议操控本机已安装的浏览器。只有 task agent 能调用 |

MCP 服务在 `plugin/plugins/mcp_adapter/` 下的 `mcp_adapter` 插件里配置，网关把它们的工具提供给 task agent。

架构参见 [Agent 系统](/zh-CN/architecture/agent-system)，主服务器代理参见 [Agent API](/zh-CN/api/rest/agent)。
