# 插件互调调用链与链上能力鉴权：实施计划

> **文档性质：approved proposal（P0–P4 的实施计划）。** P0、P1 随第一个 PR 落地，其余阶段各自另开 PR，落地后在对应小节补「落地说明」。代码和测试是权威；本页和代码对不上时以代码为准。
>
> 设计 / 需求方：wislap（插件 SDK 负责人）。设计 v2 的判定规则、粒度、调用链、插件侧视图、Origin、开放问题的结论都是既定方向，本页只把它们落到文件和函数，不重新讨论。

## 0. 一句话

插件 B 调插件 A（`ctx.plugins.call_entry` / `trigger_plugin_event`）时，由**宿主**记下「谁调了谁、最早由什么触发」，A 在用敏感能力（读记忆、读对话、推消息）时，宿主按「链上每个插件都被用户确认过这项能力」来判定；B 没确认过的能力，不能借 A 的手用到。P1 只记录（observe），P2 起才拦。

## 1. 现状核实（参考事实）

提交点：`b5bfe31e7`。

| 参考事实 | 结论 | 证据 |
|---|---|---|
| 内置插件之间没有真实的 call_entry 互调 | 属实。`plugin/plugins` 下只有 `mcp_adapter` 一处调 `trigger_plugin_event`；`proactive_controller` 里出现的 `call_entry` 只在模块说明文字里 | `plugin/plugins/mcp_adapter/__init__.py:1979`；`plugin/plugins/proactive_controller/__init__.py:8` |
| `mcp_adapter` 用 `trigger_plugin_event("adapter_call")`，全仓没有注册这个事件的处理器 | 属实，这条路走不通：`TRIGGER_CUSTOM` 只查已注册的 `events_by_type[event_type]`，全仓（含测试夹具）没有 `adapter_call` 的 `@custom_event` | `plugin/plugins/mcp_adapter/__init__.py:1979-1985`；`plugin/core/entry_executor.py:57-62`（`registered_only`）；`plugin/core/host.py:1221`（`events_by_type`） |
| 后端只在批量自启动时拦 consent 为 denied 的插件 | 属实。唯一的运行时检查是 `start_plugins_batch` 里的 `_skip_denied`，读失败时放行。手动启动、RPC handler、消息上行、`RpcDispatcher` 都不看 consent | `plugin/server/application/plugins/lifecycle_service.py:1629-1640`；`plugin/server/application/plugins/consent_service.py:154-166` |

另外两条和设计直接相关的现状：

- **插件自报的链字段不可信。** 宿主在 `_route_comm` 只覆写 `from_plugin` 和 `_rpc_connection_id`，`_rpc_trace_id` / `_rpc_parent_request_id` / `_rpc_depth` 原样透传（`plugin/core/communication.py:875-890`）。
- **另一个插件也能经 HTTP 发 `/runs`。** `mcp_adapter` 就是这么调别的插件的（`plugin/plugins/mcp_adapter/__init__.py:1773`）。这条路宿主分不出调用方是哪个插件，见第 9 节「已知缺口」。

## 2. 相关链路的落点

### 2.1 TRIGGER / TRIGGER_CUSTOM 的投递

宿主生成每次投递的 `req_id`，子进程把它当作本次调用的 `request_id`，嵌套请求再把它作为 `_rpc_parent_request_id` 带回来。所以**宿主在发出投递时就知道将来回来的父 id**，登记表以它为键。

- 宿主侧消息构造：`PluginCommunicationResourceManager._trigger_local`（`plugin/core/communication.py:351-358`，`req_id` 在 352 行生成）和 `_trigger_custom_event_local`（同文件 383-405，390 行）。经 `_send_command_and_wait_local` → `RpcPeer.request`（`plugin/core/rpc.py:284`）发出，`_send_rpc_request` 把 `request_id` 改名回 `req_id`。
- 宿主包装：`PluginHost.trigger`（`plugin/core/host.py:2219`）、`PluginHost.trigger_custom_event`（`plugin/core/host.py:2256`），都不改 `args`。
- 子进程接收：`_async_command_loop` 里 `TRIGGER_CUSTOM`（`plugin/core/host.py:1700`）、`TRIGGER`（`plugin/core/host.py:1718`）→ `_execute_command`（`plugin/core/host.py:1480`）→ `execute_entry_command`（`plugin/core/entry_executor.py:178`）→ `request_scope(message)`（`plugin/core/rpc.py:236`，`request_id = req_id`）→ `EntryExecutor.invoke`。函数式 entry、Router entry、动态 entry 都在 `invoke` 里统一执行，所以在 `_execute_command` 这一层设视图能覆盖三种入口。

宿主侧调用点和对应的 Origin：

| 调用点 | 线上类型 | Origin.kind | Origin.ref |
|---|---|---|---|
| `/runs`：`RunManager._execute_run`（`plugin/runs/manager.py:620`）→ `trigger_service.trigger_plugin`（`plugin/runs/trigger_service.py:477`）→ `_execute_trigger`（同文件 391 行 `host.trigger`）。上游有 `POST /runs`、`/ws/admin run.create`、猫爪 `nekoclaw/host/plugin_runs.py`、`search_gateway`、插件安装后的试跑 | TRIGGER | `run` | `run_id` |
| `llm_tool_callback`（`plugin/server/routes/llm_tools.py:96`，200 行 `host.trigger`） | TRIGGER | `chat_tool` | 主服务给的 `call_id` |
| `reply_completed_callback`（`plugin/server/routes/llm_tools.py:62`，84 行） | TRIGGER | `chat_hook` | `host_reply.call_id` |
| `call_surface_action`（`plugin/server/application/plugins/ui_query_service.py:1636`，1770 行） | TRIGGER | `ui_action` | 宿主生成的 `run_id` |
| `handle_plugin_to_plugin`（`plugin/server/messaging/handlers/plugin_to_plugin.py:15`）→ `PluginDispatchService.trigger_custom_event`（`plugin/server/application/plugins/dispatch_service.py:180`） | TRIGGER_CUSTOM | 继承父记录的 Origin；调用方无父记录时为 `plugin_self` | 同左 |
| `trigger_custom_event_subscribers`（`dispatch_service.py:217`）← `voice_transcript_bridge.py:50` ← `app/agent_server/api_runtime.py:170` | TRIGGER_CUSTOM | `chat_hook` | 无 |
| 其他（开发脚本、未来新增的调用点） | — | `system` | 无 |

Origin 只存指针，不存原文；`speaker` 字段预留为 `None`。Origin 本期不参与放行判定。

### 2.2 RpcContext 与各 RPC handler

- `RpcContext`（`plugin/core/rpc.py:171`，frozen dataclass）由 `RpcDispatcher.dispatch`（`plugin/core/rpc.py:652`）构造，`from_plugin` 是连接绑定的身份。
- handler 登记表：`build_request_handlers`（`plugin/server/messaging/handlers/registry.py:6`）。和能力词表对应的操作：

| 操作 | handler | 能力 |
|---|---|---|
| `MEMORY_QUERY` | `handle_memory_query`（`handlers/memory.py:9`） | `memory.read` |
| `MEMORY_SEARCH` | `handle_memory_search`（`handlers/memory.py:16`） | `memory.read` |
| `MEMORY_PERSONA_GET` | `handle_memory_persona_get`（`handlers/memory.py:21`） | `memory.read` |
| `USER_CONTEXT_GET` | `handle_user_context_get`（`handlers/user_context.py:12`），SDK 侧是 `ctx.bus.memory.get` | `memory.read` |
| `CONVERSATION_REPLY_GET` | `handle_conversation_reply_get`（`handlers/conversations.py:35`） | `conversations.read` |
| `CONVERSATION_INTERACTION_GET` | `handle_conversation_interaction_get`（`handlers/conversations.py:41`） | `conversations.read` |

### 2.3 MESSAGE_PUSH 在宿主侧的路由

不是控制 RPC。SDK `PluginContext.push_message`（`plugin/core/context.py:1050`，信封在 `_build_wire_payload`，1201 行）走每个插件独占的认证上行；`create_card` / `create_view`（`plugin/sdk/shared/core/cards.py`）最终也调 `push_message`。宿主：`_consume_message_uplink`（`plugin/core/communication.py:586`）→ `_route_message`（同文件 719 行，724 行把 `plugin_id` 覆写成连接身份）→ `_forward_message`（819 行）→ `_publish_message_to_plane`。判定点放在 `_route_message` 绑定身份之后、分派之前。

### 2.4 SDK 的 push_message 和 call_entry

`Plugins.call_entry`（`plugin/sdk/shared/core/plugins.py:134`）→ `Plugins.call`（87 行）→ SDK 上下文 `trigger_plugin_event`（`plugin/sdk/shared/core/context.py:332`）→ `PluginContext.trigger_plugin_event`（`plugin/core/context.py:1600`，同步版 1535、异步版 1573）→ `_send_request_and_wait_async`（1527 行）→ `RpcPeer.request("PLUGIN_TO_PLUGIN", ...)`，在这里从 `_scope` 取父 id。同步版走 `_send_request_and_wait` 里的 `asyncio.run`。

### 2.5 现有 `current_lanlan` 的实现（call_chain 要同构）

`_CURRENT_LANLAN` 是模块级 `ContextVar`（`plugin/core/context.py:65`），`PluginContext._lanlan_scope`（583 行）在 `EntryExecutor.invoke` 里设、`finally` 里 reset，`current_lanlan` 属性（591 行）只读当前值。`call_chain` 照这个写：模块级 `_CURRENT_CALL_CHAIN`，`_call_chain_scope(...)` 上下文管理器，只读属性 `call_chain`。

### 2.6 plugin_consents.json 的读取

`PluginConsentStore`（`plugin/server/infrastructure/plugin_consent.py:16`），路径 `<config_dir>/plugin_consents.json`，进程内缓存 `_records`，`read(plugin_id)` 返回深拷贝；读失败抛 `ServerDomainError("PLUGIN_CONSENT_READ_FAILED")`。granted 的口径和 `derive_consent`（`plugin/server/domain/consent_policy.py:19`）一致：`decision == "approved"` 时取 `approved_capabilities`，否则为空。

### 2.7 能力分析器的词表

`CAPABILITIES = ("conversations.read", "memory.read", "messages.push")`（`plugin/core/capability_analysis.py:15`），规则在 `_RULES`（65-97 行）。判定只认这三个名字；词表外的能力不判。

## 3. 总体设计（P1 起生效的部件）

### 3.1 全局开关 `PLUGIN_CAPABILITY_POLICY`

`plugin/settings.py` 新增 `PLUGIN_CAPABILITY_POLICY`，环境变量 `NEKO_PLUGIN_CAPABILITY_POLICY`，取值 `off | observe | enforce`，非法值回落默认值（写法照 `NEKO_MESSAGE_PLANE_VALIDATE_MODE`）。

| 值 | 登记表 | 视图 | 判定 | 审计 | 拦截 |
|---|---|---|---|---|---|
| `off` | 不登记 | 只含自己的链，`rights_known=False` | 不调用 | 不写 | 不拦 |
| `observe` | 登记 | 宿主下发 | 调用 | 写 | 不拦，只记 `would_deny` |
| `enforce` | 登记 | 宿主下发 | 调用 | 写 | P2 起按阶段拦（P1 里等同 `observe`） |

`off` 时每个挂钩点只做一次字符串比较就返回，不建对象、不读 consent、不写盘。子进程从继承来的环境变量读同一个设置，用来决定 `push_message` 要不要带父 id（3.6）。

### 3.2 宿主侧登记表 `CallRegistry`

新模块 `plugin/core/call_registry.py`（只在宿主进程里用；单例放在 `capability_gate.registry`）。

- **键**：`(target_plugin_id, req_id)`。`req_id` 是宿主自己生成的投递 id。
- **记录** `CallRecord`（frozen）：`call_id`（= `req_id`）、`trace_id`、`depth`、`target`、`event_type`、`event_id`、`caller`（来源插件，宿主投递时为 `None`）、`parent_call_id`、`chain`（从根到自己的插件 id 元组，登记时一次算好）、`origin`（kind、ref）、`started`（monotonic）、`flags`（如 `possible_laundering`）。另有可变的 `uses: Counter[capability]`，只在登记表锁内改。
- **登记**：在 `_trigger_local` / `_trigger_custom_event_local` 生成 `req_id` 之后、发送之前登记；`finally` 里摘除（成功、失败、超时、取消都摘）。摘除时写一条「转发」审计（结果码、耗时）和一条「放行聚合」审计（按能力计数）。
- **查找**：`lookup(plugin_id, parent_request_id)`。`plugin_id` 永远是连接绑定的身份（`RpcContext.from_plugin` 或 `_route_message` 覆写后的 `plugin_id`），`parent_request_id` 只是查表的键。插件把父 id 伪造成别的插件的 `req_id`，键里的 `plugin_id` 对不上，查不到，按「插件自发」处理：借不到别人的权限，也不能把别人卷进自己的链。
- **存活判断**：`has_live_chain(plugin_id)`：该插件当前是否作为「被别的插件调用的一方」存在于某条**还没结束**的记录里。用于 `possible_laundering`：一次使用只归到插件自己（没带父 id，或带的是它自己一条单跳记录的 id，例如 B 同时在处理一个直接的 run 和 A 发来的调用，拿 run 的 id 读记忆，就把 A 甩掉了），而它此时正被别的插件调用，就打这个标记。两条并发的多跳链（A→B 和 C→B）之间不互相比较，这个留给 P2。并发时合法的单跳调用也可能被标出来，只记不拦。
- **刚结束的记录**：`push_message` 走自己的上行通道，可能在调用结果之后才到宿主。记录摘除后最多再保留 10 秒（最多 1024 条，超出时最早的先淘汰），只供查表，让晚到的推送仍按原链判定；这期间的放行不再计入聚合，也不算「正在被调用」。这条规则对**所有**带父 id 的查表都生效（推送和控制 RPC 一样）：调用方超时或取消后，插件里还没停下的入口发出的请求，只要这条记录还没过期、也没被淘汰，就仍归到原链；10 秒是最长保留时间，不是保证。
- **上限**：存活记录最多 4096 条，满了不登记、计数 `registry_full`（审计接口返回这个数），调用照常进行（这时候它的子调用按插件自发处理）。P2 要定满了以后怎么处理。
- **并发**：宿主里多个 owner loop 和路由 loop 都会碰它，用 `threading.Lock`，临界区只有字典操作。

宿主调用点用 `delivery_link(CallLink(...))` 说明这次投递的 Origin 和父记录（任务内的上下文值，只在同一个协程、同一个 loop 里有效）。`PluginCommunicationResourceManager.trigger` / `trigger_custom_event` 在**调用方的 loop 上**读出它，再作为显式参数 `call_link=` 交给跨 loop 执行的 `_trigger_local` / `_trigger_custom_event_local`，所以不依赖 contextvar 跨 loop。P0 测试表明 contextvar 在 3.11 下其实能经 `run_coroutine_threadsafe` 带过去（见 5.1），但这依赖 `Handle` 的实现细节，不作为设计前提；有测试证明跨 loop 时用的是显式参数。

落地说明（和最初的计划不同）：计划原本让 `call_origin` / `call_parent` 作为关键字参数穿过 `PluginHost.trigger`、`dispatch_service.trigger_custom_event`、`trigger_service`。实现时发现仓库里有十几处测试替身实现了 `trigger(entry_id, args, timeout)` 这个公开签名，第三方代码也可能有；改成上面的做法后公开签名一个都没动，也就不再和 #3771 改同一批函数。

插件互调时的链：`handle_plugin_to_plugin` 先 `lookup(ctx.from_plugin, ctx.parent_request_id)` 得到调用方记录；查到则新记录 `chain = 调用方.chain + (target,)`、Origin 继承；查不到则 `chain = (caller, target)`、Origin 为 `plugin_self`，并在调用方当时 `has_live_chain` 时给新记录打 `possible_laundering`。**调用方插件本身总在链上**，因为它是连接绑定的身份；丢父 id 只会丢掉它更上游的调用方。

### 3.3 判定纯函数

新模块 `plugin/core/capability_policy.py`（宿主和 SDK 共用，不 import 服务端代码）：

```python
def decide(capability: str, chain: tuple[str, ...], granted: Callable[[str], frozenset[str]]) -> Decision
```

- `capability` 不在 `CAPABILITIES` → `Decision(judged=False, allowed=True)`。
- 否则对链上每个插件（含最后一个，即被调方自己）查 `granted(p)`，缺这项能力的进 `blocked_by`（保持链序）。
- `reason`：链上**除自己以外**有缺的 → `caller_chain`；只有自己缺 → `self_not_approved`；都不缺 → `None`。理由：P2 只强制 `caller_chain`，P3 才强制 `self_not_approved`，`caller_chain` 优先能让 P2 的拦截不受「自己也没确认」干扰。
- `granted` 读失败（consent 文件坏了）按空集处理，并在审计里标 `consent_unavailable`；observe 下不影响调用，enforce 下按失败放行还是失败拒绝，在 P2 的 PR 里定。

**observe 下失败不改变调用**：登记、查表、读 consent、写审计任何一步抛异常，都只记异常类型（不记内容），调用照常进行；P1 的 `check` 从不往外抛。有注入故障的测试。

宿主所有判定点都调同一个入口 `capability_gate.check(plugin_id, parent_request_id, capability, operation)`（`plugin/server/application/plugins/capability_gate.py`）：查表 → 组链（查不到时链 = `(plugin_id,)`，打 `unattributed`，`has_live_chain` 时再打 `possible_laundering`）→ `decide` → 记审计。P1 不拦，拦截逻辑在 P2 才加。**它不读 `_ctx`，也不读插件递交的视图**。

### 3.4 RPC handler 的判定入口（对偶）

- `RpcContext` 加字段 `parent_request_id: str | None = None`（有默认值，不影响现有构造）。`RpcDispatcher.dispatch` 从请求里取 `_rpc_parent_request_id`，只接受长度不超过 128 的字符串，否则为 `None`。
- `build_request_handlers` 里用同一个包装器 `capability_checked(capability, handler)` 包上 2.2 表里的六个操作，能力名来自一张表 `CAPABILITY_BY_OPERATION`。包装器保留 `owns_deadline` 标记。六个 handler 的判定入口完全一样，新增同类 handler 只需在表里加一行；有一条测试断言「表里的每个操作都被包了，且没有别的操作被包」。
- 包装器第一步只比较一次开关，`off` 时直接调原 handler，不查表、不读 consent、不写审计。包装器保留原 handler 的 `owns_deadline` 等标记。

### 3.5 MESSAGE_PUSH 的判定入口

`_route_message` 在 724 行之后：开关不是 `off` 时，`parent = msg.pop("_rpc_parent_request_id", None)`，再 `capability_gate.check(self.plugin_id, parent, "messages.push", "MESSAGE_PUSH")`。observe 下照常转发；P2 起拒绝时不转发、写审计。`pop` 保证下游（state 记录、消息平面、`proactive_bridge`）看到的字节和现在一样。

### 3.6 SDK 侧带父 id

`PluginContext.push_message` 在**做完大小检查之后**、放进上行队列之前，开关不是 `off` 且当前有未关闭的 `_scope` 时，在信封顶层加 `_rpc_parent_request_id`。放在大小检查之后，所以本地「是否超限」的判断和现在完全一样；宿主在写消息平面前把它去掉，平面上量到的大小也不变。

SDK 侧的同步预检（`push_message` 先看 `ctx.call_chain.allows("messages.push")`）属于拦截，放到 P2。

### 3.7 插件侧视图 `self.ctx.call_chain`

- 宿主在开关不是 `off` 时，往 TRIGGER / TRIGGER_CUSTOM 消息**顶层**加 `_call_chain`（不进 `args`，`args` 和 `_ctx` 一个字节都不变）：`trace_id`、`call_id`、`depth`、`origin{kind, ref}`、`hops[[plugin_id, event_type, event_id], ...]`、`rights`（链上各插件 granted 的交集，登记时的快照）。
- 子进程 `_execute_command` 外面套 `ctx._call_chain_scope(msg.get("_call_chain"))`，设模块级 `_CURRENT_CALL_CHAIN`。入口跑在 `invoke` 里 `create_task` 出来的任务中，会拷到这个值；三种入口都覆盖。
- `PluginContext.call_chain` 返回 frozen 的 `CallChainView`：`trace_id`、`call_id`、`depth`、`origin`（`kind`、`ref`、`speaker=None`）、`hops`、`callers`（链上除自己以外的插件 id）、`rights`、`rights_known`、`allows(cap)`。
- **永远不返回 `None`。** 不在调用上下文时（后台任务、定时器、开关 `off`）返回只含自己的链：`hops=((self, None, None),)`、`callers=()`、`depth=0`、`origin.kind="plugin_self"`、`rights=frozenset()`、`rights_known=False`。
- **`allows(cap)` 只收紧不放大**：返回 `rights_known and cap in rights`。宿主没下发权限（不知道）时返回 `False`；不在词表里的能力名也返回 `False`。插件要区分「确实没有」和「不知道」就看 `rights_known`。SDK 文档写明：`allows()` 只是参考，最终由宿主判定；`False` 不代表宿主一定会拒绝（比如 `unattributed` 的使用今天照样放行），`True` 也不代表之后不会被拒绝（`rights` 是投递时的快照）。
- 和 `request_scope` 同样处理「入口结束后还活着的后台任务」：scope 对象带 `closed` 标记，入口结束时置位，之后读到的是只含自己的链。这样视图和宿主的归属判断一致（后台任务的请求在宿主那边也是 unattributed）。
- 现有的 `plugin.sdk.shared.runtime.call_chain`（进程内的 `CallChain` / `get_call_chain`）不动，二者无关；P1 文档里写清楚区别。
- 「入口显式声明名为 `call_chain` 的关键字参数时注入」是设计里的可选项，P1 不做（会动 `prepare_entry_kwargs`），放到 P2 和 SDK 文档一起评估。

视图只给插件看，宿主判定永远不读它；不做签名。

### 3.8 审计

新模块 `plugin/server/application/plugins/call_audit.py`：内存环形缓冲 + 追加式 JSONL，**不走事件总线**，全部用标准库现成组件。

- **路径**：`resolve_plugin_log_directory(log_subdir="plugin_call_audit") / "call_audit.jsonl"`，和插件服务日志同一个根。
- **写入**：专用 logger（`propagate=False`，只写一行一个 JSON）挂 `QueueHandler`，由 `QueueListener` 在它自己的守护线程里交给 `RotatingFileHandler`（4 MiB 轮转，保留 3 个，总量约 16 MiB）。队列 2048 条，满了丢弃并计数（`dropped`），不阻塞宿主 loop。环形缓冲是 `deque(maxlen=1000)`，判定记录另有一个 `deque(maxlen=500)`，频繁的转发记录挤不掉它们；接口合并两者按时间返回。
  - 隐私规则禁止的是「用 logger 记隐私内容」。审计记录里只有 id、能力名和结果码，没有任何内容原文，所以用专用 logger 写不违规；模块说明里写了这一点，并有测试断言审计不含隐私内容。
- **退出**：插件服务关停时（`plugin/server/lifecycle.py` 的 `_shutdown_internal`，宿主都停下之后）放入停止标记并等监听线程把队列写完，最多 1 秒，超时就不等了，不阻塞关停（没用 `QueueListener.stop()`：它等待没有时限，队列满时还会直接报错）。关停之后的记录只进环形缓冲；同一进程里服务重启时重新打开。监听线程是守护线程，解释器退出不等它；`os._exit` 或外部强制结束时没写完的记录会丢，这是尽力而为。
- **去重**：同一 `(plugin, capability, verdict, reason, blocked_by, flags)` 的判定记录 60 秒内只写第一条（一个 dict 存上次写的时间）。没确认过 consent 的插件（例如从没手动启动过的内置插件）每次用能力都会 `would_deny self_not_approved`，不去重会刷屏。

记录种类和字段：

| 字段 | forward | decision | allow_summary |
|---|---|---|---|
| `ts`（UTC ISO）、`kind`、`mode` | ✓ | ✓ | ✓ |
| `trace_id`、`call_id` | ✓ | ✓（有链时） | ✓ |
| `parent_call_id`、`depth`、`target` | ✓ | — | ✓ |
| `caller`、`event_type`、`event_id`、`origin_ref`、`result`（`ok` 或错误码）、`duration_ms` | ✓ | — | — |
| `origin_kind`、`flags` | ✓ | ✓ | — |
| `plugin`、`operation`、`capability`、`chain`、`verdict`（`allow` / `would_deny`）、`reason`、`blocked_by` | — | ✓ | — |
| `counts`（能力 → 次数） | — | — | ✓ |

`flags` 的取值：`unattributed`、`possible_laundering`、`consent_unavailable`。

写入点：

- `forward`、`allow_summary`：登记表摘除时（`_send_delivery` 的 `finally`）。宿主直接投递（`/runs`、工具、UI）也写，链长为 1。
- `decision`：`capability_gate.check`。`would_deny` 和带 `possible_laundering` 的放行立即写（按上面去重）；有链的放行只给记录的 `uses` 计数，摘除时进 `allow_summary`；没有链、也不可疑的放行（定时器、后台任务）不写。

**不记**参数、返回值、对话正文、异常原文。`result` 只写错误码（`PluginError` 的 `code`，或 `PLUGIN_EXECUTION_FAILED` 这类固定码），`event_id` 是插件代码里的入口名，不是用户内容。测试用带哨兵字符串的参数、返回值、异常消息、push 正文跑一遍，断言 JSONL 和环形缓冲里都找不到哨兵。

### 3.9 本机只读的审计接口

- 路由：`GET /plugin-audit/call-chain`，新文件 `plugin/server/routes/plugin_audit.py`，在 `build_plugin_server_app`（`plugin/server/http_app.py:277`）里 `include_router`。
- 不带参数，返回两个环形缓冲里现存的全部记录（转发最多 1000 条加判定最多 500 条，新的在前，不读盘；不再二次截断，免得判定被转发挤掉）：`{"mode", "entries": [...], "dropped", "registry_full"}`。
- 鉴权：插件服务只绑 `127.0.0.1`（`plugin/user_plugin_server.py:172`）。路由再加一个严格本机依赖：复用 `plugin/server/infrastructure/mutation_auth.py:115` 的 `_local_request`（客户端是回环地址、Host 是回环、没有转发头），不满足就 403。带转发头的代理请求会被拒；但如果反向代理把请求转成不带转发头的本机连接，应用分不出来，所以**部署时不要把这个路由暴露给非本机客户端**。带 `Origin` 的请求、以及 `Sec-Fetch-Site` 不是 `none` 的请求（浏览器页面发起的；同源 GET 不带 `Origin`，但会带 `Sec-Fetch-Site: same-origin`）也一律 403；Node、Electron 主进程的 fetch 只带 `Sec-Fetch-Mode`，不受影响，所以即使 CORS 放行了本机前端的来源，页面也读不到它；只给本机的命令行和脚本用。
- 开关 `off` 时返回 `mode: "off"` 和空列表。

### 3.10 错误码 `CAPABILITY_NOT_GRANTED`

常量定义在 `plugin/core/capability_policy.py`（`CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"`）和构造 `details` 的函数：`{"capability", "blocked_by": [...], "reason": "self_not_approved" | "caller_chain"}`。P1 只定义；observe 下不会返回给调用方，有测试断言这一点。P2 起由 `capability_gate.check` 以 `ServerDomainError(code, ..., 403)` 抛出，经 `request_router._domain_error` 变成 RPC 错误，SDK 侧映射成 `PluginCallError.code`。

## 4. 默认开关值

**P1 的默认值为 `observe`**（2026-11-18 定，可推翻：需求方要 `off` 时只改一个默认值）。

- 设计要求观察期至少一个正式版，退出判据是 `would_deny` 里没有未解释的借道项。默认 `off` 的话正式版里几乎没人会手动打开，观察期收不到数据。
- observe 下插件可观察的行为不变：不拦任何调用；`args`、`_ctx`、push 信封到达下游时的字节都不变；新增的只有 TRIGGER 顶层 `_call_chain` 字段（子进程消费、不进入口参数）和一个只读属性。
- 代价：每次投递多一次登记 / 摘除（加锁的字典操作）；每次用敏感能力多一次 consent 读（进程内缓存的深拷贝）；审计写盘有总量上限，由后台线程做。
- 退路：`NEKO_PLUGIN_CAPABILITY_POLICY=off` 立即回到现在的行为，不需要迁移任何数据。

`enforce` 在 P2 合并前不建议作为默认值；P2 合并后默认值是否切到 `enforce`，按观察期数据另定。

## 5. 分阶段计划

### 5.1 P0：钉住不变式（只加测试）

新文件 `plugin/tests/unit/core/test_call_chain_invariants.py`。测试如实反映现状，带不上的就钉住「带不上」：

| 场景 | 现状（3.11.18 实测） | 测试断言 | P1 怎么处理 |
|---|---|---|---|
| 宿主：`run_coroutine_threadsafe` 跨 loop（`_run_on_owner_loop`） | contextvar 带得过去（`Handle` 捕获了调用方上下文） | 带得过去 | 不依赖它，宿主内部显式传参 |
| 插件：入口里 `asyncio.to_thread(同步函数)` 再嵌套调用 | 父 id 带得上 | 带得上 | 正常归属 |
| 插件：同步 API 在工作线程里走 `asyncio.run`（`_send_request_and_wait`） | 带得上（`asyncio.run` 拷贝调用线程的上下文） | 带得上 | 正常归属 |
| 插件：`loop.run_in_executor(None, ...)` | **带不上** | 父 id 为 `None` | 宿主按插件自发处理，打 `unattributed`，若有存活链再打 `possible_laundering`；SDK 文档建议用 `asyncio.to_thread` 或 `contextvars.copy_context().run` |
| 插件：裸 `threading.Thread` | **带不上** | 父 id 为 `None` | 同上 |
| 插件：入口结束后仍在跑的后台任务 | 带不上（scope 已关闭，现有测试 `test_rpc_peer.py::test_background_request_uses_its_own_budget_after_entry_scope_ends` 已钉住预算部分） | 父 id 为 `None` | 同上；视图也回到只含自己的链 |

回退：纯测试，无。风险：无。

### 5.2 P1：登记表 + 视图 + 审计 + observe（本 PR）

改动点（落地后的实际位置）：

| 文件 | 函数 / 位置 | 改动 |
|---|---|---|
| `plugin/settings.py` | 模块级 | `PLUGIN_CAPABILITY_POLICY`（不进 `PUBLIC_SYSTEM_CONFIG_KEYS`，插件读到的系统配置不变） |
| `plugin/core/capability_policy.py`（新） | `decide`、`Decision`、`current_mode`、错误码常量 | 纯函数和开关读取 |
| `plugin/core/call_registry.py`（新） | `CallRegistry`、`CallRecord`、`CallLink`、`Origin`、`delivery_link` | 登记表和调用点的链信息 |
| `plugin/sdk/shared/core/call_chain_view.py`（新） | `CallChainView`、`view_from_wire`、`self_only_view`、`call_chain_scope` | 视图，子进程用（放在 SDK 里：SDK 不 import `plugin.core`） |
| `plugin/server/application/plugins/capability_gate.py`（新） | `check`、`capability_checked`、`CAPABILITY_BY_OPERATION`、`begin_call` / `end_call`、`link_for_plugin_call` | 判定入口、登记 / 摘除 |
| `plugin/server/application/plugins/call_audit.py`（新） | `record`、`entries`、`close` | 环形缓冲 + 标准库 QueueHandler / QueueListener / RotatingFileHandler |
| `plugin/core/rpc.py` | `RpcContext`、`RpcDispatcher.dispatch`、`current_request_id`、`bounded_parent_request_id` | 加 `parent_request_id` |
| `plugin/core/communication.py` | `trigger`、`_trigger_local`、`trigger_custom_event`、`_trigger_custom_event_local`、`_send_delivery`（新）、`_route_message`、`_check_message_push`（新） | 登记 / 摘除，下发 `_call_chain`；MESSAGE_PUSH 判定 |
| `plugin/core/host.py` | 子进程 `_execute_command` | 套视图 scope |
| `plugin/core/context.py` | `call_chain` 属性；`push_message` 在大小检查后带父 id | |
| `plugin/sdk/shared/core/context.py` | `SdkContext.call_chain` | SDK 门面转发 |
| `plugin/server/messaging/handlers/registry.py` | `build_request_handlers` | 六个操作包上判定 |
| `plugin/server/messaging/handlers/plugin_to_plugin.py` | `handle_plugin_to_plugin` | 查父记录，设 `delivery_link` |
| `plugin/runs/manager.py` | `_execute_run` | `run` Origin |
| `plugin/server/routes/llm_tools.py` | 两个回调 | `chat_tool` / `chat_hook` Origin |
| `plugin/server/application/plugins/ui_query_service.py` | `call_surface_action` | `ui_action` Origin |
| `plugin/server/application/plugins/voice_transcript_bridge.py` | `resolve_voice_transcript_request` | `chat_hook` Origin |
| `plugin/server/routes/plugin_audit.py`（新）、`plugin/server/http_app.py` | 路由 + 注册 | |
| `plugin/server/lifecycle.py` | `_shutdown_internal` | 有时限地刷审计队列 |
| `docs/plugins/sdk-reference.md` 及 zh-CN、ja 版 | `ctx.call_chain` 一节 | 插件文档 |

测试（只覆盖主流路径和需求列出的不变式）：

- `plugin/tests/integration/test_call_chain_processes.py`（三个真实插件子进程 + 真实控制路由）：三种入口都读到链、登记后摘除；跨插件调用延长链；借道记 `would_deny caller_chain` 且不拦；`self_not_approved` 和放行聚合；MESSAGE_PUSH 判定且到达消息平面的记录不变；`unattributed` / `possible_laundering`；伪造父 id 和 `_ctx` 既借不到权限也卷不进别人；审计不含参数、返回值、push 正文、异常文本；`off` 时登记、判定、审计都没被调用，线上消息不变。
- `plugin/tests/unit/server/test_call_chain.py`：`decide`；视图永不为 `None`、未知时 `allows()` 为 `False`、入口结束后回到只含自己；查表按插件区分；六个操作同一个包装器；observe 下内部出错不改变调用；跨 loop 用的是显式参数；审计用标准库轮转写入 JSON 行；审计接口只给本机。
- `plugin/tests/unit/core/test_call_chain_invariants.py`：P0。

回退：开关 `off`。审计文件是新增的独立文件，删掉不影响任何东西；没有改任何已有数据格式。

风险：

- 登记 / 摘除漏在某条异常路径上 → 记录泄漏。由 `finally` 摘除（成功、报错、超时、取消都走它）和上限兜底。
- 判定时同步读 consent：批量自启动前已在线程里读入内存，之后每次约 4µs；但读和保存共用一把锁，用户确认或拒绝、写入小 JSON 文件的那几毫秒里，判定会在事件循环上等这把锁。P1 接受这个窄窗口；如果实际观察到阻塞，再改成读已发布的内存快照。consent 文件损坏、读不出来时，5 秒内不再重读，期间按「未知」处理，避免每次判定都在事件循环上重读文件。
- 审计写盘出错 → 只影响审计（标准库 handler 自己处理写错误），不影响调用。
- `RpcContext` 加字段 → 有默认值；现有测试里直接构造它的地方不受影响。

### 5.3 P2：强制防借道

- `enforce` 下，`capability_gate.check` 在 `decision.caller_blocked` 时：RPC 抛 `CAPABILITY_NOT_GRANTED`；MESSAGE_PUSH 不转发、写 `deny` 审计。`self_not_approved` 和 `unattributed` 仍然放行。
- SDK `push_message` 用 `ctx.call_chain.allows("messages.push")` 同步预检，拒绝时返回 `{"ok": False, "submitted": False, "reason": "capability_not_granted"}`（和现有 `payload_too_large` 同一套返回形状），不抛异常；宿主仍做最终判定。
- SDK：`PluginCallError.code == "CAPABILITY_NOT_GRANTED"` 的文档和示例；视需要做 `call_chain` 关键字参数注入。
- 归属更严（P1 只记录，P2 拦截前要定）：
  - P1 只按插件带来的那一条记录判定，只在「归到它自己、而它正被别的插件调用」时标 `possible_laundering`。是否改为取该插件所有存活记录链的并集来判定（防止挑一条窄的），以及两条并发多跳链怎么处理。
  - 调用方超时或取消后，插件里的入口可能还在跑。P1 在记录摘除后 10 秒内仍按原链归属（3.2「刚结束的记录」），超过 10 秒按插件自发处理。拦截时这个窗口够不够、要不要按入口实际结束来摘除。
  - 登记表满了以后是拒绝投递还是把这类请求一律当作可疑。
  - 没有链的放行（定时器、后台任务）P1 不写审计，要不要按插件汇总。
- 补上 P1 没覆盖到的 `conversations.read` 入口：把 `bus.conversations.get` / `get_message` 挪到需要认证的控制 RPC（新增两个 handler，挂同一个判定包装器），SDK 对外接口不变，消息平面上的旧入口保留一个版本；要压测读对话的延迟和背压（今天这条路有独立的并发槽）。
- 测试：借道被拒、自己没确认仍放行、unattributed 放行；拒绝不改变没有借道的调用；SDK 预检和宿主判定一致。
- 回退：开关回到 `observe` 或 `off`。
- 风险：误拦正常的互调（今天内置插件里没有，第三方插件不清楚）——这正是观察期要排除的。前置条件：观察期至少一个正式版，`would_deny` 里没有未解释的 `caller_chain` 项；插件作者公告（第 6 节）至少提前一个正式版发出。

### 5.4 P3：强制自检

- 前置：运行时观察到、但用户没确认过的能力并入 consent 报告（`capability_analysis_service` 的报告加 `observed` 来源，`derive_consent` 把它算进 `unconfirmed`），经现有待确认通知（`consent_notices`）引导用户确认。
- `enforce` 下 `self_not_approved` 也拒绝。用独立的阶段设置（例如 `NEKO_PLUGIN_CAPABILITY_SELF_CHECK=observe|enforce`），可以单独回退。
- 测试：observed 能力进入待确认；确认后放行；拒绝时错误码和 `reason`。
- 回退：阶段设置回 `observe`。
- 风险：从没确认过 consent 的插件（尤其自启动的内置插件）全部用不了敏感能力。必须先解决「内置插件的 consent 从哪来」（预置还是首次使用时询问），这一项要需求方和维护者拍板，放在 P3 的 PR 里定。
- 界面文案会有新增（待确认通知里「运行时发现」的说明），八种语言同步。

### 5.5 P4：宿主不经手的能力

- 范围：插件进程内截屏、直接请求主服务改设置、经 HTTP 调 `reply_tail.register` / `subscribe`、经消息平面 RPC 读对话、经 HTTP 发 `/runs` 调别的插件。这些宿主要么看不到，要么分不出调用方。
- 做法：词表扩充用于**披露**（分析器能识别的，加进 `CAPABILITIES` 并在 consent 界面展示）；运行时按**插件粒度**兜底（没确认就不给对应的令牌 / 不启动），不做链上判定。
- 文档写明：这套机制**不防恶意插件**。插件和宿主之间没有沙箱，插件进程能 import 宿主模块、能直连本机端口，见 `.agent/skills/plugin-host-pitfalls/references/host-boundaries.md` 第 3 节。它防的是「低权限插件经正常 API 借高权限插件的手」。
- 回退：每一项独立开关。
- 文案：consent 界面新增能力名称，八种语言同步。

## 6. 插件面向变更的公告流程

仓库的做法（#3683）：插件面向的较大变更开一个长期的 RFC issue，结构是「给插件作者」开头提要 → 概要 → 为什么 → 结构 → 影响范围（按插件使用者 / 插件开发者 / 第三方前端分表，开发者部分分「没变的 / 行为变了 / 已删除 / 新增」）→ 作者自查清单（全答「否」就不用做事）→ 还没定、想听意见 → 分期表 → 修订记录 → 英文摘要。每个相关 PR 合并或开出来时在 issue 下回一帖，正文跟着更新并记修订。

本计划的安排：

- **P1**：对插件是纯新增（只读属性 `ctx.call_chain`），不改现有行为。PR 描述写清楚；插件 SDK 文档（三语）加 `ctx.call_chain` 一节。合并后开 RFC issue，说明整个 P0–P4 的计划、P2 会拦什么、作者怎么自查（「你的插件是否经 `call_entry` 让别的插件替你读记忆 / 读对话 / 推消息」「是否在 `run_in_executor` / 裸线程里调别的插件」），并公布 observe 期的时长。
- **P2**：拦截前至少一个正式版发出公告；P2 的 PR 开出时在 RFC issue 回帖。
- **P3、P4**：同上，各自回帖并更新正文。

RFC issue 以项目对外的口径发，需要维护者同意后再开（第 10 节「已自行处理、可推翻」）。

## 7. 多语言

P1 **没有用户可见的界面文案**：开关是环境变量，审计接口返回结构化数据，`CAPABILITY_NOT_GRANTED` 在 observe 下不出现。所以 P1 不改 `i18n` 文件。插件 SDK 文档按 `docs/plugins`、`docs/zh-CN/plugins`、`docs/ja/plugins` 三语同步（仓库插件文档现有的语种）。

P2 起如果 SDK 预检的拒绝原因要在界面显示、P3 的待确认通知、P4 的 consent 能力名称，都是用户可见文案，八种语言（en、ja、ko、zh-CN、zh-TW、ru、pt、es）同步改。

## 8. 和 PR #3771 的关系

#3771（`output_schema`）改了 `entry_executor.invoke` / `execute_entry_command`、`dispatch_service.trigger_custom_event` 的异常分支、`trigger_service._execute_trigger` 的异常分支、`plugin/core/registry.py`、SDK 装饰器。落地后本 PR 和它**不改同一个函数**：

- 视图 scope 放在子进程 `host.py` 的 `_execute_command`，不改 `entry_executor.py`。
- 链信息走 `delivery_link`（3.2 落地说明），不改 `dispatch_service`、`trigger_service`。
- 文件层面都改了 `docs/*/plugins/sdk-reference.md`，位置不同（#3771 在入口一节，本 PR 在跨插件调用一节）。#3771 合并后用 merge 合 main。

## 9. 已知缺口（P1 不覆盖，已排进后续阶段）

| 缺口 | 为什么 P1 管不到 | 排在哪 |
|---|---|---|
| `ctx.bus.conversations.get` / `get_message` 走消息平面 RPC（`plugin/message_plane/rpc_server.py:219`），不走控制路由 | 消息平面的回环 RPC 没有调用方身份（同文件 183 行的注释），宿主分不出是哪个插件。**P1 的审计看不到经这条路读对话的行为，观察期的 `would_deny` 数据不包含这一类** | P2：把这两个操作挪到需要认证的控制 RPC，挂同一个判定包装器（第 10 节第 2 条） |
| `reply_tail.subscribe` / `register` 走 HTTP 到主服务，`plugin_id` 由插件自报 | 宿主不经手 | P4 |
| 插件经 HTTP 发 `POST /runs` 调别的插件（`mcp_adapter` 在用） | 宿主只看到一次 `run`，不知道是哪个插件发的；被调插件的链 = 自己 | P4（或给 `/runs` 加调用方令牌，另议） |
| 插件进程内截屏、直连主服务改设置 | 宿主不经手 | P4 |
| `run_in_executor`、裸线程、入口结束后的后台任务里发出的请求 | 父 id 带不上（5.1） | 按插件自发处理 + `possible_laundering` 标记；只记不拦（已拍板） |

## 10. 已定事项（2026-11-18）

1. **P1 默认开关值**：`observe`。开销：每次投递一次加锁的字典操作，每次用能力读一次进程内缓存的 consent；审计写盘总量约 16 MiB。关闭：`NEKO_PLUGIN_CAPABILITY_POLICY=off`。可推翻。
2. **经消息平面读对话**：放进 P2，挪到需要认证的控制 RPC（5.3）。P1 不改，观察期数据不含这一类（第 9 节）。
3. **视图在调用上下文之外**：返回只含自己的链，不返回 `None`；`allows()` 未知时返回 `False`，另给 `rights_known` 区分「没有」和「不知道」（3.7）。
4. **审计写线程的退出**：队列满了丢弃并计数；插件服务关停时在有时限内（1 秒）尽力把队列刷进文件，写线程是守护线程，不会卡住进程退出；`os._exit` 之类的强制退出路径不保证落盘（3.8）。
6. **复用现成能力、最小实现、范围收敛**（2026-11-18）：审计用标准库的 QueueHandler / QueueListener / RotatingFileHandler；去重和聚合用 dict；P1 不为 P2–P4 预埋代码（错误码常量除外）。自审时考虑过的「调用方超时后记录再保留一段时间」「同一插件两条并发多跳链互相比较」「没有链的放行按插件汇总」「登记表满了单独写审计」都移到 P2 再定。
5. 环境变量名 `NEKO_PLUGIN_CAPABILITY_POLICY`；`reason` 的优先级 `caller_chain` 在前。

---

**English summary.** This is the implementation plan for host-maintained call chains between plugins, capability checks along the chain, and an audit trail. When plugin B calls plugin A and A then reads memory, reads conversations or pushes a message, the host decides using its own registry (keyed by the connection-bound plugin id and the host-generated delivery id), never the plugin-supplied `_ctx` or `_rpc_*` fields: the use is allowed only if every plugin on the chain has that capability approved in `plugin_consents.json`. P0 pins context-propagation invariants with tests. P1 adds the registry, a read-only `ctx.call_chain` view, a ring buffer plus rolling JSONL audit, the shared decision function in observe mode (records `would_deny`, blocks nothing), the `PLUGIN_CAPABILITY_POLICY` switch (`off | observe | enforce`, recommended default `observe`), the `CAPABILITY_NOT_GRANTED` code (defined only), and a loopback-only read endpoint. P2 enforces anti-laundering, P3 enforces self-checks after runtime-observed capabilities join the consent flow, and P4 covers capabilities the host does not mediate. This does not defend against malicious plugins.
