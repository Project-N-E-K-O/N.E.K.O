# 三服务器设计

独立开发启动命令分别是 `uv run python -m app.main_server`、`uv run python -m app.memory_server` 和 `uv run python -m app.agent_server`。每条命令执行对应 package 的 `__main__.py`；FastAPI app 与实现保留在 package 内。

## 主服务器（`app/main_server/`，端口 48911）

`app/main_server/__main__.py` 配置 Uvicorn，并运行由 `app/main_server/__init__.py` 与 `web_app.py` 组装的 FastAPI `app`。`character_runtime.py` 拥有逐角色 runtime slot。

### 启动 ownership

主服务器：

1. 解析存储启动状态并初始化 `ConfigManager`；
2. 按需导入 cloud-save 状态；运行时数据发生变化时要求记忆服务器重新加载；
3. 加载角色数据，为每个角色创建或保留 `LLMSessionManager`、WebSocket lock、queue 与 sync connector task；
4. 初始化 Steam/Workshop 集成和动态静态资源 mount；
5. 启动主服务器侧 Agent ZeroMQ bridge、后台 warmup、游戏清理与 token tracking；
6. 在 `127.0.0.1:48911` 暴露组装后的 router 与 WebSocket endpoint。

如果存储选择被阻断，服务器会保持 limited mode，直到浏览器释放启动 barrier。因此启动并不是固定的 import 清单。

### 拥有的职责

- 浏览器页面及挂载的前端、模型和 Workshop 资源；
- REST router 与 `/ws/{lanlan_name}` 浏览器会话；
- 每个角色一个 `LLMSessionManager`；
- 显式的文本 `OmniOfflineClient` 或音频 `OmniRealtimeClient` 模型会话；
- 外部 TTS worker 线程及前端 48 kHz PCM 投递；
- 用于 Memory/Agent 控制的 HTTP client 与主进程拥有的 ZeroMQ socket；
- 用于记忆持久化和可选 Monitor mirror 的逐角色 cross-server stream。

相关原生 realtime provider 路径以 24 kHz 输出音频，由会话管理器重采样到 48 kHz。外部 TTS worker 已按应用输出 PCM 契约返回数据。

## 记忆服务器（`app/memory_server/`，端口 48912）

记忆服务器负责按角色持久化记忆。实时工作上下文保留在主服务器的 LLM 会话内；记忆服务器接收已完成的对话轮次、维护持久化视图、渲染新会话上下文，并响应显式召回请求。

### 持久化数据与派生索引

| 数据 | 用途 | 后端 |
|---|---|---|
| 近期历史 | 滑动对话窗口及 LLM 压缩备忘 | 每个角色的 `recent.json` |
| 时间索引原文 | 按时间保存的源对话历史 | SQLite `time_indexed_original` 表 |
| 事实 | 带来源和处理元数据的抽取陈述 | `facts.json` 及平面 `facts_archive.json` |
| 反思 | 按证据评分的观察，具有 pending、confirmed、promoted/merged、denied、archived 状态 | `reflections.json` 及归档分片 |
| 人格 | 新会话上下文会渲染的持久化角色/用户画像 | `persona.json` 及归档分片 |
| 召回索引 | 针对候选记忆的 BM25 与可选本地 ONNX 向量 | 派生缓存，不是事实来源 |

旧 SQLite `time_indexed_compressed` 表只为兼容保留，不再写入新数据。近期摘要保存在 `recent.json`，长期抽象已由事实、反思和人格承载。

### 关键操作

- **接收并结算**已完成的对话轮次，同时保留带时间戳的原始历史
- **压缩**近期滑动窗口，但不替换按时间保存的源记录
- **抽取并整理**事实、检测证据、合成反思，并把稳定观察晋升或合并进人格
- 为 `/new_dialog` **渲染**人格、可用反思与近期上下文
- 通过纯时间查询或 BM25/可选向量混合召回与倒数排名融合（RRF）来**按需召回**；这条延迟敏感的工具路径不额外调用 LLM 重排
- 通过游标、outbox、事件日志、reconciler、衰减与归档扫描实现**恢复和审计**
- 通过 `/memory_browser` **审阅近期历史**；该页面不直接编辑事实、反思或人格

`app/memory_server/__main__.py` 是独立 Uvicorn 入口；launcher 也可以 import 并挂载该 package。Storage startup barrier 可以让重写入的 runtime 工作保持 limited，直到主服务器确认活动存储根。

完整生命周期及“自动注入/按需召回”的边界请参阅[记忆系统](/zh-CN/architecture/memory-system)。

## Agent 服务器（`app/agent_server/`，端口 48915 和 48916）

`app/agent_server/__main__.py` 在 `127.0.0.1:48915` 启动 package 的 Tool Server FastAPI app。实现拆分在 `api_runtime.py`、`api_routes.py`、`capabilities.py`、`results.py`、`plugin_host.py`、`nekoclaw_mount.py` 和 `nekoclaw_builtins.py`。负责记录和执行任务的猫爪（NekoClaw）宿主在 `nekoclaw/host/`。

Agent 启动时，进程会初始化 capability state、Agent 侧 ZeroMQ bridge 和 agent 模型的连通性检查，并登记猫爪宿主。登记不创建任何东西：宿主的台账、agent runtime 和能力网关都等到第一次用到时才创建。`plugin_host.py` 在独立线程中启动 `127.0.0.1:48916` 的内嵌 user-plugin FastAPI listener。即使 listener 与 Agent 同进程，user-plugin 执行仍受 feature flag 与 plugin lifecycle gate 控制。

### HTTP ownership

- **`:48915` Tool Server** —— Agent flag/capability、主对话两个任务工具背后的内部派发和查询路由、任务查看、取消、health，以及 deferred 完成回调。
- **`:48916` 内嵌 user-plugin 服务** —— 已安装插件发现、run lifecycle、market bridge 目标和 deferred plugin completion 支持。

主服务器把公开 `/api/agent/*` surface 代理到这些内部服务。浏览器代码应把主服务器响应视为权威状态，而不是调用进程本地对象。

### ZeroMQ ownership

| 默认地址 | 模式 | Bind owner | 方向 | 用途 |
|---|---|---|---|---|
| `tcp://127.0.0.1:48961` | PUB / SUB | Main | Main → Agent | Session 与 lifecycle 事件 |
| `tcp://127.0.0.1:48963` | PUSH / PULL | Main | Main → Agent | 可靠 `turn_check_request` 队列 |
| `tcp://127.0.0.1:48962` | PUSH / PULL | Main（`PULL`） | Agent → Main | ACK、状态、任务更新与汇报 |

Agent 连接对应的 SUB/PULL/PUSH 镜像 socket。Bridge 使用后台接收线程包装同步 ZeroMQ socket。Agent → Main 投递没有 HTTP fallback；关于任务的汇报留在宿主的 outbox 里重试，直到 bridge 收下为止。

### 任务执行路径

1. 主对话模型调用 `dispatch_task` 工具。`main_logic/core/tool_calling.py` 把这句话 POST 到 `:48915` 的 `/nekoclaw/dispatch`，再把回执交还给模型。
2. 任务大脑（`nekoclaw/host/task_brain.py`）在台账里登记一件委托。第一轮在本地完成，大约一秒内给出回执；第二轮在后台进行。
3. 决策模型选出插件入口，经插件服务的 `/runs` 协议直达调用。选不中，或者要分多步，就把委托交给 task agent（界面上叫「使魔」）。
4. task agent 跑在 agent runtime（`nekoclaw/runtime/`）里，经能力网关使用插件入口、MCP 工具、键鼠和浏览器。
5. 每次状态变化都作为 `task_update` 发出。结果、提问和更正先写进 outbox，再通过 `:48962` 发送；Main bridge 更新浏览器，并把模型可见的汇报排入匹配的 `LLMSessionManager`。
6. 用户发起的一轮结束、而这一轮没有调用会动手的工具时，`main_logic/cross_server.py` 通过 `:48963` 发出 `turn_check_request`；主 bridge 等待 ACK，超时后重试一次。

叫停时先把台账里的状态写成 `cancelled`，再取消插件的 Run 或关掉 task agent 的会话，所以迟到的输出盖不掉已叫停的委托。插件返回 deferred 结果时，委托保持 `working`，直到 completion endpoint 被调用，或超时后标为失败。

派发、事后检查、flag 和投递语义请参阅 [Agent 系统](/zh-CN/architecture/agent-system)。
