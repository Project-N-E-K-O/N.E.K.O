# 智能体系统

智能体系统（界面上叫「猫爪」，NekoClaw）负责让角色在对话之外把事情办成。要不要办，由主对话模型决定，并通过一次工具调用说出来；记账、决定谁来办、执行和汇报，由智能体服务器里的猫爪宿主负责。系统不再在每轮对话结束后自动分析：旧的逐轮分析器和它的几个执行通道已经删除。

下文有两个固定叫法：**委托**（代码名 `commission`）是她交办的一件事；**task agent** 是分多步去办一件委托的执行会话。界面上把 task agent 叫作「使魔」，代码、路由和字段值里一律是 `task_agent`。

## 运行时组件

```text
主服务器                                      智能体服务器 (:48915)
┌───────────────────────────┐                 ┌──────────────────────────────────┐
│ LLMSessionManager         │  HTTP           │ 猫爪宿主 (nekoclaw/host/)        │
│  工具：dispatch_task      │ ──────────────> │  ├─ 任务大脑                     │
│        query_tasks        │ <──── 回执 ──── │  │   台账 · 决策模型             │
│ cross_server.py           │                 │  ├─ task agent 管理              │
│ MainServerAgentBridge     │  ZeroMQ         │  ├─ 能力网关                     │
│ pending_agent_callbacks   │ ── 事后检查 ──> │  └─ 权限                         │
│                           │ <──── 汇报 ──── │ 内嵌插件服务 (:48916)            │
└───────────────────────────┘                 └──────────────────────────────────┘
```

每个智能体进程只有一个宿主。启动时只登记宿主怎么读本进程的开关（`app/agent_server/nekoclaw_mount.py`）；台账文件、agent runtime 和网关监听都等到第一次用到时才创建。

## 两个任务工具

总开关和至少一个能力开关打开时，`main_logic/core/tool_calling.py` 会给主对话模型注册两个内置工具。参数都只有一句自然语言，不带编号；这句话说的是哪一件委托，由宿主判断。工具定义在 `config/prompts/prompts_task_tools.py`。

| 工具 | 参数 | 用途 |
|---|---|---|
| `dispatch_task` | `task` | 派发：交办一件事。补充、改主意、叫停、转达用户对提问的回答也走它 |
| `query_tasks` | `question` | 查询：在办什么、某件委托办得怎样、某件事能不能办。留空就列出在办的事 |

处理函数调用智能体服务器的 `POST /nekoclaw/dispatch` 和 `POST /nekoclaw/query`，最多等 2.5 秒；连不上智能体服务器时，工具结果会写明这件事没有交出去；请求发出去了却没等到回应时，写明不确定有没有交出去（智能体服务器可能照样在办），并让模型稍后用查询确认，不要重新派发。主动搭话的请求不带这两个工具。

## 宿主内部

### 任务大脑

- **台账。** 一个 SQLite 文件 `<数据根>/nekoclaw/ledger.db`，存委托、委托的事件、待发汇报（outbox）和任务偏好。委托的状态是 `queued`、`working`、`waiting_user`、`done`、`blocked`、`failed`、`cancelled` 之一，只在一个函数里迁移；`done` 和 `cancelled` 不能再变，所以迟到的结果盖不掉叫停。
- **派发第一轮（等）。** 在本地判断这句话是叫停、回答、补充，还是和在办的事重复；用关键词和 BM25 看有没有候选插件；看 task agent 能不能开。回执是「已在做」「没有相关能力」（附原因）「受理」之一。这一轮不调模型，唯一的例外是本地没有候选、task agent 又开不了时跑一次小模型粗筛。超过 1 秒先回「收下了」，结论随后作为一条汇报送达。
- **派发第二轮（后台）。** 决策模型（`nekoclaw/host/decision.py`）用一次模型调用选出插件入口并填好参数。全部插件描述合计超过 3000 token 时先粗筛：关键词命中、BM25 前 10 名、小模型粗筛三者取并集。插件优先：一个入口就能办完的事，直接经插件服务的 `/runs` 协议调用；选不中，或者要分多步，才交给 task agent。模型还可以答插件和 task agent 都办不了、或者这根本不是一件要办的事，这时什么都不启动。答了不存在的插件或入口时不追问，按选不中处理。这一轮推翻了回执时，会立即告诉她。
- **查询。** 任何情况下都不调模型。进展直接读台账；「能不能办」沿用第一轮的本地匹配，不立案。

### task agent

一个 task agent 是内置 agent runtime（`nekoclaw/runtime/`）里的一个会话；agent runtime 跑在子进程里，用到时才拉起。一件委托一个会话，同时最多 10 个在办，其余的委托停在 `queued`。task agent 用 agent 模型，拿到的是她派发的那句话和用户原话，不是对话历史；它还可以再开自己的 task agent。task agent 向用户提问或需要权限批准时，委托进入 `waiting_user`，直到她用 `dispatch_task` 把回答转达过来。

### 能力网关与权限

除了自带的文件和命令工具，task agent 用到的其它能力都经能力网关（`nekoclaw/host/gateway.py`）取得。网关是回环地址上的一个 MCP 服务，第一个需要它的 task agent 启动时才开始监听，一件委托一个令牌。它提供：

- 对筛选可见的插件入口，每个入口一个工具，和直达调用走同一段 `/runs` 代码；
- `mcp_adapter` 插件里配置的 MCP 服务的工具；
- 键鼠、浏览器两项内置能力。各接受一条指令，由 `brain/computer_use.py`、`brain/browser_use_adapter.py` 里现有的循环执行；只有 task agent 能调用。

权限存在 `<数据根>/nekoclaw/permissions.json`，每一类是 `first_ask`（首次询问）、`always_ask`（每次都问）、`allow`（放行）、`deny`（禁止）之一。默认放行插件入口和工作目录内的文件读写；跑命令、在工作目录外写文件、键鼠、浏览器、每个 MCP 服务默认首次询问。设成 `deny` 的插件或 MCP 服务，直达调用也不会用到。

## 汇报回流

每条汇报先写进台账的 outbox，再经 ZeroMQ 发给主服务器。发送失败后 1 秒重试，之后每次翻倍，最长 5 分钟；上一个进程没发出去的汇报，在宿主下一次被用到时补发。

| outbox 类型 | 含义 | 发送时的事件 |
|---|---|---|
| `task_result` | 委托怎么结束的 | `task_result` |
| `question` | 委托在等用户回答或批准 | `proactive_message` |
| `correction` | 第二轮或事后检查发现她说的和实际不符 | `proactive_message` |
| `receipt` | 第一轮超过 1 秒后补发的结论 | `proactive_message` |

主服务器把汇报放进对应 `LLMSessionManager` 的队列，按现有的投递方式送达：`proactive` 唤醒她开口，`passive` 在她下一轮带上，`silent` 的结果不向她汇报。文本会话可以立即投递；语音会话可能延迟到注入或热切换边界。她转述汇报的那一轮不会再送去做下面的事后检查。

对话会话每隔几轮就会换一个，自己留不住任务记录。每个新会话都会调用 `GET /nekoclaw/brief`，得知哪些事还在办。委托的每次变化同时作为 `task_update` 事件发出，供[任务 HUD](/architecture/task-hud-system) 显示。

## 事后检查

用户发起的一轮结束、而她这一轮没有调用会动手的工具时（回忆、查知识、`query_tasks` 不算），`main_logic/cross_server.py` 会发出 `turn_check_request`，带上用户这句话和她的回复。`publish_turn_check_reliably()` 短暂等待 `turn_check_ack`，超时重试一次。她自己发起的轮次、智能体回调轮次、只有图片的轮次不发。

宿主先过一道不花钱的门：输入时的意图分数已知且低于 0.2、用户的话里没有命中任何插件关键词、而且每个可被筛选的插件都声明了关键词，这三条同时成立就跳过。过了门的轮次花一次小模型调用，结论四选一。

| 结论 | 处理 |
|---|---|
| 没事，或她有意不办 | 什么都不做 |
| 她说做不到，但本地匹配找得到办法 | 提醒她，由她自己更正 |
| 她答应了，却没派发 | 替她派发，并告诉她 |
| 她说办完了，台账里没有这件委托 | 提醒她，由她自己更正 |

检查本身没有执行权：只有她派发了，或口头答应了，才会立案。

## 传输映射

HTTP 负责控制和查询，监听 `127.0.0.1:48915`；内嵌插件服务监听 `127.0.0.1:48916`。事件走同步 ZeroMQ socket，在后台线程接收，因此也兼容 Windows Proactor 事件循环。

| 默认地址 | 模式 | 方向 | 用途 |
|---|---|---|---|
| `tcp://127.0.0.1:48961` | PUB / SUB | 主 → Agent | 会话和生命周期事件 |
| `tcp://127.0.0.1:48963` | PUSH / PULL | 主 → Agent | 可靠的 `turn_check_request` 队列 |
| `tcp://127.0.0.1:48962` | PUSH / PULL | Agent → 主 | ACK、状态、任务更新和汇报 |

端口可分别通过 `NEKO_ZMQ_SESSION_PUB_PORT`、`NEKO_ZMQ_ANALYZE_PUSH_PORT` 和 `NEKO_ZMQ_AGENT_PUSH_PORT` 覆盖。Agent → 主服务器的事件没有 HTTP 降级路径：汇报留在 outbox 里，直到桥接收下为止；`task_update` 和状态事件只尽力发送。

## 能力状态

权威状态通过主服务器的 `/api/agent/*` 代理暴露。前端通过 `/api/agent/command` 修改状态，通过 `/api/agent/state` 刷新状态，不应把本地复选框当作权威来源。

| 状态 | 含义 |
|---|---|
| `analyzer_enabled` / UI `agent_enabled` | 总开关 |
| `computer_use_enabled` | 给 task agent 用的键鼠能力 |
| `browser_use_enabled` | 给 task agent 用的浏览器能力 |
| `user_plugin_enabled` | 插件入口，直达调用和经网关调用都受它控制 |

Manager 和智能体服务器构造时这些开关均为关闭。首次真实 `greeting_check` 后会恢复持久化的运行时意图，因此“构造时关闭”不表示每次刷新页面都会重置用户选择。键鼠和浏览器还要求 agent 模型的连通性检查通过。

## 叫停、上限与重启

- 叫停时先把状态写成 `cancelled`，再取消插件的 Run（调用插件服务的 `POST /runs/{run_id}/cancel`）或关掉 task agent 的会话。`POST /admin/control` 的 `end_all` 和关闭总开关会叫停全部在办的委托。
- 键鼠、浏览器各自同一时间只执行一次调用。
- 插件可以返回 `deferred: true`。委托保持 `working`，直到 `/api/agent/tasks/{task_id}/complete` 被调用；一小时没等到就标成 `failed`。
- 宿主重启后，上一个进程留下的 `queued`、`working`、`waiting_user` 委托标成 `failed`，随她的下一轮汇报；deferred 的委托保留到期限为止。
- 智能体进程给 HUD 用的任务视图会把已结束的任务保留五分钟。

## 实现映射

| 关注点 | 当前实现 |
|---|---|
| 任务工具和处理函数 | `main_logic/core/tool_calling.py`、`config/prompts/prompts_task_tools.py` |
| 轮次结束时触发事后检查 | `main_logic/cross_server.py` |
| ZeroMQ 桥及 ACK/重试 | `main_logic/agent_event_bus.py` |
| 派发、查询、事后检查 | `nekoclaw/host/task_brain.py` |
| 插件筛选和决策模型 | `nekoclaw/host/selection.py`、`decision.py` |
| 台账、outbox、汇报 | `nekoclaw/host/ledger.py`、`outbox.py`、`reports.py` |
| task agent 和 agent runtime | `nekoclaw/host/task_agents.py`、`nekoclaw/runtime/` |
| 能力清单、网关、权限 | `nekoclaw/host/capabilities.py`、`gateway.py`、`permissions.py` |
| 调用插件入口（`/runs`） | `nekoclaw/host/plugin_runs.py` |
| 挂载宿主、HUD 视图、内置能力 | `app/agent_server/nekoclaw_mount.py`、`nekoclaw_builtins.py` |
| Agent 生命周期和 HTTP API | `app/agent_server/api_runtime.py`、`api_routes.py` |
| 结果投递到聊天 | `main_logic/core/proactive.py` |

公共控制端点见[智能体 REST API](/zh-CN/api/rest/agent)，内部路由见 [Agent Server API](/zh-CN/api/agent-server)。
