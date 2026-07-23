# 主动搭话领域模块化重构技术设计

状态：第一阶段后端重构及 6.1 模块化复审收口已完成。本文最初基于 2026-07-15 的仓库结构制定计划，并于 2026-07-22 按实际落地结果同步模块结构、兼容边界和验收状态；前端内部拆分仍不在本阶段范围内。

一句话结论：主动搭话领域逻辑已下沉到 `main_logic/proactive_chat/`；Router 只保留 HTTP、CSRF、请求解析、组装副作用和响应适配；通用采集能力继续留在 `utils/`；Prompt 模板继续留在 `config/prompts/prompts_proactive.py`；第一阶段保持 `static/app/app-proactive.js` 及全部现有前端和插件协议兼容。

## 实施状态（2026-07-22）

- 迁移路线 0～6 已完成：协议、状态、决策、生成、小游戏邀请、音乐推荐、分阶段编排和薄 Router 均已落地。
- 6.1 模块化收口已完成：新增 `sources.py`，闭合 generation 的模型调用入口，消除领域模块导入时注册 hook 的副作用，并将旧音乐 helper 兼容导出退回 Router。
- 主流程 canonical owner 已从 Router 移至 `main_logic/proactive_chat/service.py`；Router 通过显式 collaborator 注入框架能力。
- 对照重构前分支完成了行为兼容修复：保留旧 helper 关键字调用和旧导出、恢复 WebSocket 的 falsey/异常传播与业务日志语义、恢复 Router 注入的 `memory_dir`。
- `state.py` 仍是可变状态、锁和持久化算法的唯一所有者；旧 Router 路径只保留同对象 re-export 或注入兼容薄包装，不存在双份状态或双写。
- 路线 7 的前端内部拆分继续独立排期，不作为本阶段完成条件。

## 重构前基线

原单体 `main_routers/system_router.py` 当时已经完成第一轮按路由领域拆包，主动搭话相关实现主要分布在：

```text
main_routers/system_router/
  __init__.py                 # 兼容门面和旧内部符号 re-export
  proactive_chat_flow.py      # /proactive_chat、music_played_through 与主流程
  proactive_parsing.py        # Phase 1 解析、标签清理、响应体构造
  proactive_history.py        # 搭话历史、素材历史、计数和相似度
  proactive_sources.py        # source 历史、权重和选择辅助
  proactive_content.py        # source 内容日志和格式化
  mini_game_invite.py         # 小游戏邀请状态、投递、反馈及 HTTP 路由
  break_reminders.py          # 休息提醒等相邻子流程
```

基线规模约为：

- `proactive_chat_flow.py`：2933 行。
- `mini_game_invite.py`：837 行，并已有大规模独立单元测试。
- `proactive_parsing.py`：548 行。
- `proactive_history.py`：465 行。
- `proactive_sources.py`：276 行。

因此本次不是再次“拆分单体 Router”，而是把 Router 包中已经形成的主动搭话领域模块迁入 `main_logic/proactive_chat/`，建立稳定的领域边界和单向依赖。

前端真实兼容入口为 `static/app/app-proactive.js`；`static/app-proactive.test.cjs` 是对应的 Node 契约测试。后续文档和实现不得再使用旧路径 `static/app-proactive.js` 指代入口文件。

## 目标与非目标

### 目标

- 让 `main_routers/system_router/` 只承担 HTTP 和框架适配职责。
- 让主动搭话的协议契约、状态、决策、生成、子能力和编排拥有明确的领域归属。
- 保持用户行为、公开 API、WebSocket 事件、插件事件和前端全局 API 不变。
- 每个迁移步骤都可单独回归、单独回滚，不引入双份状态。
- 将测试逐步从 Router 内部实现迁移为对领域模块和公开协议的直接验证。

### 非目标

- 不重写或重调主动搭话策略。
- 不改变默认开关，不新增或重命名设置字段。
- 不改变 Phase 1/2 Prompt 文案和模型 tier。
- 不把主动搭话专属逻辑迁入 `utils/`。
- 不在第一阶段拆分前端实现。
- 不把 `main_logic/proactive_delivery.py` 或 `main_logic/session_state.py` 并入主动搭话包。
- 不借模块迁移修改反馈权重、邀请概率、去重阈值或播放完成语义。

## 已落地结构

```text
main_logic/proactive_chat/
  __init__.py
  contracts.py               # action / reason_code / stage、命令与领域结果
  state.py                   # 搭话、素材、source 历史，计数、持久化、相似度
  decisions.py               # 入口 gate、activity gate、source 选择、PASS 判定
  generation.py              # Phase 1/2、解析、输出清理和生成保护
  delivery.py                # 投递提交、成功后记录和生命周期收口
  mini_game_invite.py        # 邀请状态、命中、冷却、选择和投递结果
  music_recommendation.py    # 音乐推荐、严格约束、链接和播放完成反馈
  break_reminders.py         # anti-slack、休息提醒和组合邀请子流程
  content_logging.py         # 主动搭话 source 内容日志辅助
  sources.py                 # source 获取、并发收集、原始链接归一化
  service.py                 # 主流程编排，不依赖 FastAPI 对象
```

不引入 `channels/` 中间层：

- `mini_game_invite.py` 明确表示本模块只拥有小游戏邀请，而不是整个小游戏系统。
- `music_recommendation.py` 明确表示本模块只拥有主动音乐推荐，而不是整个音乐播放系统。
- 主动搭话包本身已经提供足够的领域上下文，无需再增加抽象含混的目录层级。

继续保留的 Router 适配层：

```text
main_routers/system_router/
  proactive_chat_flow.py     # 主动搭话 HTTP 路由、请求/响应及 WS 适配
  mini_game_invite.py        # 邀请反馈 HTTP 路由和协议适配
  break_reminders.py         # Router 注入适配与旧入口兼容
  proactive_history.py       # 状态对象 re-export 与注入目录兼容包装
  proactive_sources.py       # source 状态 re-export 与注入目录兼容包装
  proactive_parsing.py       # 协议/解析旧路径兼容门面
  proactive_content.py       # 内容 helper 旧路径兼容门面
  __init__.py                # 过渡期 re-export，按调用方逐步收缩
```

依赖方向必须保持为：

```text
Router adapters
  -> proactive_chat.service
    -> contracts / state / decisions / generation / delivery
       / mini_game_invite / music_recommendation
       / break_reminders / content_logging
      -> proactive_delivery / session_state / config / utils
```

`main_logic/proactive_chat/` 禁止反向导入 `main_routers.system_router`、FastAPI `Request`、`JSONResponse` 或共享 Router 对象。

## Service 边界

Router 必须完成请求读取、CSRF/本地变更校验和 HTTP 响应适配，再把与框架无关的命令交给 service。目标形态如下：

```python
@router.post("/proactive_chat")
async def proactive_chat(request: Request):
    _validate_local_mutation_request(request)
    data = await _read_json_object(request)
    command = ProactiveChatCommand.from_payload(data)
    result = await proactive_service.handle(command)
    return JSONResponse(result.body, status_code=result.status_code)
```

`ProactiveChatCommand`、`ProactiveChatResult` 优先定义在 `contracts.py`；如果某个类型只服务于编排内部，也可以就近定义在 `service.py`。不为仅有一两个类型过早新增 `models.py`。

Service 的纯业务 helper 不得接收 FastAPI 对象；只有 Router 适配函数可以操作请求、响应头和 HTTP 状态码。

## 重构前模块到落地模块的映射

| 重构前内容 | 落地归属 | 迁移说明 |
|---|---|---|
| `proactive_parsing.py` 中 reason/stage 常量和响应构造 | `contracts.py` | 先迁移并由旧模块 re-export。 |
| Phase 1 结果解析、PASS sentinel、screen/intent 标签清理 | `generation.py` | 与模型输出保护放在一起。 |
| `proactive_history.py` 中搭话历史、素材历史、计数、相似度 | `state.py` | 新模块成为唯一 canonical owner。 |
| `proactive_sources.py` 中 source 历史加载和持久化 | `state.py` | 不再跨模块直接读取可变全局字典。 |
| `proactive_sources.py` 中权重、过滤和跳过决策 | `decisions.py` | 只通过 state 查询接口读取历史。 |
| `proactive_content.py` 中 Prompt 输入格式化 | `generation.py` | 仅迁移主动搭话专属格式化；通用抓取仍留在 `utils/`。 |
| `proactive_content.py` 中纯日志辅助 | `content_logging.py` | Router 旧路径保留兼容门面；不得记录原始隐私对话到 logger。 |
| `mini_game_invite.py` 中邀请状态和业务规则 | `main_logic/proactive_chat/mini_game_invite.py` | HTTP 路由仍留在 Router。 |
| music source、约束、链接和 played-through 处理 | `music_recommendation.py` | 底层抓取继续调用 `utils.music_crawlers`。 |
| locale resolution、follow-up topic hooks | `decisions.py` 或 `generation.py` | 按“决定是否/选什么”与“生成什么”分配，并增加直接测试。 |
| meme moderation、vision staging | `generation.py` 与通用 `utils` | 领域策略下沉，通用图像处理不迁移。 |
| `break_reminders.py` | `main_logic/proactive_chat/break_reminders.py` | 领域实现下沉；Router 只注入配置和保留旧入口。 |
| `proactive_chat_flow.py` 主流程 | `service.py` | 分阶段抽取，最后将 Router 收口为薄适配器。 |
| delivery commit、成功后记录和生命周期结束 | `delivery.py` | 与底层 `main_logic/proactive_delivery.py` 分层：前者拥有主动搭话阶段，后者仍拥有通用投递队列。 |

## 模块边界

| 内容 | 目标归属 | 说明 |
|---|---|---|
| token、语言、JSON 原子读写、日志、内部 HTTP client | `utils/` | 多业务复用的基础设施。 |
| web/music/meme/screenshot 抓取和处理 | `utils/` | 通用采集能力，不决定主动搭话策略。 |
| HTTP 路由、CSRF、Request/JSONResponse、响应头 | `main_routers/system_router/` | Router 只保留协议和框架边界。 |
| `/api/proactive/*` 设置路由 | `main_routers/proactive_router.py` | 现有公开 API 和字段语义不变。 |
| reason_code、stage、action、命令和领域结果 | `contracts.py` | 主动搭话协议与领域契约。 |
| 搭话历史、素材历史、source 历史、计数持久化 | `state.py` | 主动搭话状态的唯一所有者。 |
| gate、source 权重和选择、PASS 判定 | `decisions.py` | 决定本轮是否继续及使用哪些来源。 |
| Phase 1/2、输出清理、防泄漏、防复读保护 | `generation.py` | 生成链路与模型输出保护。 |
| 小游戏邀请 | `mini_game_invite.py` | 领域状态和规则；HTTP 入口留在 Router。 |
| 音乐主动搭话 | `music_recommendation.py` | 领域选择和反馈；底层抓取复用 `utils`。 |
| anti-slack 与休息提醒 | `break_reminders.py` | 领域规则和投递子流程；配置实例由 Router 注入。 |
| 主动搭话内容日志 | `content_logging.py` | 仅记录既有 source 诊断信息，不扩大隐私日志。 |
| 投递提交、成功后记录、生命周期结束 | `delivery.py` | 只在 commit 成功后更新历史和计数。 |
| 主流程编排 | `service.py` | 串联各阶段，不包含 HTTP 框架对象。 |
| 投递队列与提交 | `main_logic/proactive_delivery.py` | 保持独立，由 service 调用。 |
| 生命周期状态机 | `main_logic/session_state.py` | 保持独立，由 service 调用。 |
| 前端 timer、leader election、截图采集、source cards | `static/app/app-proactive.js` | 第一阶段保持兼容。 |
| 插件 `proactive_message` 协议 | `plugin/server/messaging/proactive_bridge.py` 与 `app/main_server.py` | 不并入主动搭话包。 |

## 状态所有权与兼容 re-export

迁移期间必须避免双份状态：

- `state.py` 成为搭话历史、素材历史、source 历史、计数和锁的唯一 canonical owner。
- 旧 Router 模块只能 re-export 同一个可变对象，或用薄包装把 Router 注入依赖传给 canonical 函数；不得复制一份字典、deque、锁、loaded flag 或持久化算法。
- 不得在旧模块和新模块分别加载或持久化同一份 JSON 文件。
- `state.py` 的持久化 API 接受可选 `memory_dir`：主流程显式传入 Router 的配置实例目录，直接领域调用保留共享 singleton fallback；这不是多租户或运行时热切换存储根目录能力。
- `decisions.py` 不直接 import `state.py` 的可变全局字典，应通过查询函数或只读快照访问。
- 迁移后测试应优先 patch 实际消费依赖的领域模块，而不是 patch `system_router.__init__` 的快照式 re-export。
- 对布尔 loaded flag 等会重新绑定的值，不承诺通过旧门面保持实时同步；相关调用方应迁移为查询函数。

兼容 re-export 只是过渡机制。每一轮迁移都要记录旧符号的剩余调用方，并在后续 PR 中逐步清理，不能把 `system_router/__init__.py` 永久变成全仓内部 API。

## 公开协议与兼容契约

### HTTP API

重构期间必须保持：

- `POST /api/proactive_chat`
- `POST /api/proactive/music_played_through`
- `POST /api/mini_game/invite/respond`
- `GET /api/proactive/mode`
- `POST /api/proactive/mode`
- `GET /api/proactive/settings`
- `POST /api/proactive/settings`

除 URL 和方法外，还必须保持：

- 成功、PASS、校验失败和运行异常对应的 HTTP 状态码。
- CSRF、本地请求校验和远程部署限制行为。
- `Cache-Control` 等现有 no-store 响应头。
- `/api/proactive_chat` 返回体中的 `action`、`reason_code`、`stage`、`message`、`source_links`。
- 小游戏反馈接口中的 `session_id`、`action`、`game_type`、`launch_url` 等现有字段和错误语义。

所有 API URL 继续遵守无末尾斜杠约定。

### 前端 API

必须继续导出：

- `window.scheduleProactiveChat`
- `window.resetProactiveChatBackoff`
- `window.appProactive`

### WebSocket 与插件事件

必须保持：

- WebSocket 事件 `mini_game_invite_options`
- WebSocket 事件 `mini_game_invite_resolved`
- 插件事件 `proactive_message`

事件的 `type`、`session_id`、`action`、`game_type`、`launch_url`、`options` 等已存在字段应纳入契约测试；可选字段仍保持现有可选性，不得在重构中擅自改为必填。

### 设置字段

主动搭话设置继续以当前字段为准：

```text
proactiveChatEnabled
proactiveVisionEnabled
proactiveVisionChatEnabled
proactiveNewsChatEnabled
proactiveVideoChatEnabled
proactivePersonalChatEnabled
proactiveMusicEnabled
proactiveMemeEnabled
proactiveMiniGameInviteEnabled
proactiveChatInterval
proactiveVisionInterval
```

`proactiveVisionEnabled` 是用户专有字段，语义上是前端“隐私模式”开关的反面。主动搭话 preset 和 `/api/proactive/*` 写路径不得覆盖它。

## 行为不变量

以下语义必须在每一个迁移 PR 中保持：

- 所有主动搭话 `reason_code -> stage/action` 映射稳定且可穷举验证。
- 并发抢占、用户打断、delivery busy、route active、voice fast path 行为不变。
- `PROACTIVE_START` 成功后，每条退出路径最终都且只触发一次等价的 `PROACTIVE_DONE` 清理。
- delivery commit 成功后才能写入主动搭话历史、素材历史和成功计数。
- 投递失败、抢占或生成失败不得污染历史、计数、topic usage 或 anti-repeat 语料。
- 小游戏邀请只有成功投递后才写入主动搭话历史并更新计数。
- 小游戏 pending、回应、冷却和跨窗口 resolved 广播语义不变。
- 音乐完整播放反馈只清理 history 中 `channel == "music"` 的通道标记，不删除历史文本。
- `ignored`、`mini_game_ignored` 等反馈只作为报告层压力信号，不在本次重构中变成即时自动降权。
- Phase 1 PASS、Phase 2 空输出、标签泄漏、重复文本和超时的返回语义不变。

生命周期和投递清理应逐步收敛到 service 的统一 `finally`、幂等结束函数或异步上下文管理器中，避免迁移后继续依靠大量分支手动清理。

## 迁移路线

### 0. 固化基线与依赖清单（已完成）

实施任何移动前：

- 记录当前主动搭话相关模块、路由、前端调用、WebSocket 事件和插件事件。
- 为所有 `reason_code`、`stage`、`action` 建立参数化契约测试。
- 为 HTTP 状态码、CSRF、no-store header 和小游戏反馈接口建立路由契约测试。
- 执行完整目标测试集，保存基线结果。
- 明确每个旧内部符号的实际调用方，区分运行时兼容和仅测试兼容。

### 1. 迁移协议契约（已完成）

先建立 `contracts.py`，迁移：

- `_proactive_stage_for_reason`
- `_proactive_response_body`
- `_proactive_pass_body`
- `_proactive_chat_body`
- `_proactive_error_body`
- `_ensure_proactive_reason_code`
- 全部 `PROACTIVE_REASON_*`、`PROACTIVE_STAGE_*` 及映射表

`proactive_parsing.py` 和 `system_router/__init__.py` 在过渡期 re-export 新实现。契约模块不得依赖 Router、状态、生成或投递模块。

### 2. 迁移状态与历史（已完成）

建立 `state.py`，迁移：

- 近期主动搭话记录、格式化和相似度判断。
- 素材 key、素材历史和素材级近期去重。
- source 使用历史、加载、持久化和查询。
- 成功投递计数、ever-delivered 标记及原子持久化。
- reminiscence usage 独立缓冲。
- music channel 标记清理。

状态迁移必须是“移动 canonical owner”，不能复制状态。旧模块先改为导入和 re-export 新对象，再迁移调用方。异步路径中的文件 I/O 继续使用异步原子读写或 `asyncio.to_thread`，不得引入同步阻塞。

### 3. 迁移决策与生成保护（已完成）

建立 `decisions.py` 和 `generation.py`：

- `decisions.py`：入口 gate、activity gate、source 权重、source 过滤、PASS 判定、locale/topic hook 选择。
- `generation.py`：Phase 1/2 调用、模型结果解析、Prompt 输入格式化、screen tag / intent label 清理、meme/vision 保护、anti-repeat 和输出 fence。

模型调用继续遵守现有模型 tier、输入 token budget、输出 budget、timeout 和不显式设置 temperature 的仓库规范。Prompt 文案仍留在 `config/prompts/prompts_proactive.py`。

### 4. 迁移小游戏邀请与音乐推荐（已完成）

建立 `mini_game_invite.py` 与 `music_recommendation.py`：

- 小游戏模块拥有邀请状态、命中、pending、回应、冷却、关键词选择和成功投递后的状态更新。
- 音乐模块拥有 source 选择、严格约束、播放链接返回和 played-through 反馈语义。
- `POST /api/mini_game/invite/respond` 和 `POST /api/proactive/music_played_through` 仍由 Router 声明并适配领域结果。
- WebSocket payload 可以由领域模块构造纯字典，但实际发送由注入的 collaborator 或外层适配器完成。

### 5. 分阶段收口主流程（已完成）

不要一次性搬运整个 `proactive_chat` 大函数。应先从当前函数逐段抽出可直接测试的阶段函数：

```text
parse command
entry guards
activity gate
source selection
mini-game short-circuit
phase1 decision
phase2 generation
dedup / text guard
delivery commit
record history / metrics
finalize lifecycle
```

阶段函数先由现有 Router 流程调用；待 Router 只剩编排后，再将编排整体移动到 `service.py`。迁移期间不得让新的 `main_logic` service 反向调用旧 Router 流程，以免形成逆向依赖或循环 import。

### 6. 收缩 Router 与兼容门面（已完成，兼容门面按调用方渐进清理）

Service 稳定后：

- `proactive_chat_flow.py` 只保留 HTTP 入口、请求读取、校验和响应适配。
- `mini_game_invite.py` 只保留小游戏反馈路由和响应适配。
- 将测试和运行时调用切换到新的 canonical 模块。
- 清理 `system_router/__init__.py` 中不再需要的内部 re-export。
- 对仍需过渡的 re-export 建立清单和删除条件。

### 6.1 模块化复审收口（已完成）

2026-07-22 按“更加模块化”而非“仅完成跨目录迁移”的标准复审后，确认外层依赖方向已经正确，但内部仍存在来源抓取、模型调用和兼容副作用混在编排中的问题。本轮只完成以下四项收口，不横向增加更多领域模块，也不调整业务策略。

#### 6.1.1 来源获取编排

新增 `main_logic/proactive_chat/sources.py`，迁移 service 内部的 `_fetch_source`、来源并发任务组装、原始链接提取和结果归一化：

- 底层仍调用 `utils.web_scraper`、`utils.screenshot_utils` 等既有实现。
- 不拥有来源权重、概率、PASS 或去重策略；这些继续属于 `decisions.py` 和 `state.py`。
- 并发顺序、timeout、异常降级、结果 shape、source mode 和 service 日志命名空间保持不变。
- `service.py` 不再直接 import news、video、home、personal、window 等具体抓取函数。
- 旧 `_extract_links_from_raw` 路径由 Router 兼容门面改为指向 `sources.py`，调用签名保持兼容。

#### 6.1.2 Generation 阶段所有权

在现有 `generation.py` 中收口模型相关实现，不新增 `phase1.py`、`phase2.py` 或 `model_runtime.py`：

- `ProactiveModelConfig` 只承载每轮已经解析出的 conversation/vision 配置；不读取全局配置，不改变模型 tier。
- `_make_proactive_llm()` 与 `_llm_call_with_retry()` 顶层化，保持 streaming、timeout、token budget、Focus thinking extra body 和 3 次重试语义。
- `_run_unified_phase1()` 拥有 unified prompt 构造、模型调用、结果解析和失败降级。
- `_fetch_phase1_followups()` 拥有关键词驱动的 music/meme 并发后置获取；service 在调用前保留原有用户抢占检查。
- `Phase2PromptContext` 在 generation 内拥有既有模板渲染；service 只组装有界字段并在原有时序调用渲染，以保持 prompt 长度日志和最终抢占检查的先后关系。
- `_run_phase2_generation()` 拥有模型消息构造、vision/thinking 选择、主 stream、格式自救和 BM25/output guard。
- service 继续组装来源、回忆、活动、屏幕和素材等业务上下文；generation 不反向读取 Router、HTTP 或最终投递状态。

该边界刻意保留 `handle_proactive_chat()` 内的 `_end_proactive`、抢占响应体和 memory 响应解析局部 helper：它们依赖本轮生命周期或局部解析上下文，不属于 source/LLM 能力；仅为缩短函数而继续拆分会增加参数搬运和间接层。

#### 6.1.3 消除领域模块导入副作用

`main_logic/proactive_chat/mini_game_invite.py` 提供幂等的 `install_mini_game_invite_hooks()`，由 `main_routers/system_router/mini_game_invite.py` 在 Router 组装时调用：

- 单独 import 领域模块不再修改全局 event bus。
- 注册仍使用既有 callback identity 去重，不改变 hook 顺序、first-hit-wins 或异常处理语义。
- Router 本来就承担路由注册等应用组装副作用，因此该调用不下沉到 `app/`，本轮没有扩大高风险模块审查范围。

#### 6.1.4 兼容导出退回 Router

`content_logging.py` 只保留自身实现的 news/video/trending/personal logger；旧 `main_routers/system_router/proactive_content.py` 分别从 `content_logging.py` 和 `music_recommendation.py` 导入并维持原导出表：

- 旧调用方 import 路径和符号不变。
- 新领域模块之间不为旧 Router 文件布局建立兼容依赖。
- 本阶段不删除 `system_router/__init__.py` 的既有 re-export。

#### 6.1.5 收口边界与验收结果

- 相对 `upstream/main`，非测试改动文件为 20 个；本轮只新增 `sources.py`，未超过仓库“20 个非测试文件”的不拆分说明门槛。
- 本轮修改 `main_logic/` 和 Router 兼容门面；没有修改 `app/` 或 `memory/`。
- `handle_proactive_chat()` 不再定义 source 或 LLM 嵌套 helper，文件净减少约 500 行；不设置机械行数目标。
- `main_logic/proactive_chat/` 仍不导入 FastAPI 或 `main_routers.system_router`，包内无反向 Router 依赖。
- Prompt 文案、来源概率、去重阈值、抢占时序、持久化目录、HTTP、WebSocket、插件、小游戏和音乐播放语义均未调整。
- action note 泄漏、`MEME]` 关键词污染和外部 TLS/302 等问题不属于本模块化收口，需单独立项并说明行为变化。

收口后的主流程仍由 service 清晰串联：

```text
entry / voice fast path
activity and schedule gate
break / mini-game short-circuit
collect sources
prepare bounded context
run Phase 1
preemption check
run Phase 1 follow-up fetches
prepare Phase 2 context
run Phase 2
commit and record delivery
finalize lifecycle
```

验收依据是职责和依赖，而不是把同一段逻辑切成更多小函数。由于涉及 `main_logic/`，自动检查不能替代人工 review；人工审核重点见下方回归报告。

### 7. 前端后续拆分（独立排期，未纳入本阶段）

前端拆分不作为第一阶段验收条件。后续如需拆分 `static/app/app-proactive.js`，建议在 `static/app/proactive/` 下建立内部模块：

```text
static/app/proactive/
  leader.js
  scheduler.js
  sources.js
  transport.js
  attachments.js
  vision.js
```

无论内部如何拆分，`static/app/app-proactive.js` 都继续作为兼容门面并导出当前 `window.*` API。

## 测试与验证

每轮迁移至少运行：

```bash
uv run python -m pytest \
  tests/unit/test_proactive_material_dedup.py \
  tests/unit/test_proactive_intent_label_leak.py \
  tests/unit/test_music_played_through_reset.py \
  tests/unit/test_mini_game_invite.py \
  tests/unit/test_proactive_phase1_pass.py \
  tests/unit/test_reflection_synthesis_loop.py \
  tests/unit/test_session_state.py \
  tests/unit/test_proactive_delivery.py \
  tests/unit/test_proactive_sm_integration.py \
  tests/unit/test_proactive_sid_guard.py \
  tests/unit/test_proactive_vision_screenshot_staging.py \
  tests/unit/test_proactive_interval_20s_rollback.py \
  tests/unit/test_proactive_agent_trigger.py \
  tests/unit/test_proactive_action_note.py \
  tests/unit/test_proactive_text_does_not_dehumanize.py \
  tests/unit/test_proactive_meme_moderation_static.py \
  tests/unit/test_system_router_topic_hooks.py

node static/app-proactive.test.cjs
uv run python scripts/check_api_trailing_slash.py
uv run python scripts/check_prompt_hygiene.py
uv run python scripts/check_llm_budget.py
git diff --check
```

2026-07-22 的 6.1 本地验证记录：

- 修改过的 Python 文件通过 AST 解析和 Ruff check。
- 直接执行 Phase 2 streaming 6 项回归函数通过；拆分过程中另有 5 项 output guard 临时验证通过，但其阶段性测试文件未保留在最终 PR。
- 最终只新增保留服务兼容边界、显式持久化根目录、小游戏 WebSocket 边界和 Phase 2 streaming 四组关键回归测试；其余拆分步骤测试由现有集成测试与人工 review 覆盖，不随 PR 提交。
- 项目 `.venv` 固定到已经从本机移除的 Python 3.11；用可用 Python 3.12 复用 site-packages 时，Pillow/greenlet 等 3.11 二进制扩展 ABI 不兼容，标准 pytest 收集无法作为有效结果。不得把该环境阻断误记为“完整测试通过”。
- 完整测试仍需在项目配置的 Python 3.11 + uv 环境中执行；当前静态和直接函数验证只覆盖本次移动的关键生成保护，不替代完整回归。

## 验收标准与结果

- Router 不拥有主动搭话领域状态和策略。
- `main_logic/proactive_chat/` 不导入 FastAPI 或 `main_routers.system_router`。
- `service.py` 不直接依赖具体 source fetcher，也不在 `handle_proactive_chat()` 中定义 source/LLM 嵌套 helper。
- Phase 1 的 prompt、模型调用和解析，以及 Phase 2 的 messages、模型 stream 和 output guard 均由 generation 顶层入口拥有。
- 单独 import `mini_game_invite.py` 不注册全局 hook；Router 组装时仍完成幂等注册。
- `content_logging.py` 不依赖 `music_recommendation.py`；旧 Router 导出保持不变。
- 所有主动搭话 `reason_code`、`stage`、`action`、HTTP、WebSocket 和插件协议保持稳定。
- 并发抢占、用户打断、delivery busy、route active、voice fast path、持久化和投递语义保持不变。
- 音乐完整播放反馈仍只清理 music channel 标记，不删除历史文本。
- 小游戏邀请仍只在成功投递后计入主动搭话历史和计数。
- 前端仍通过原有 `window.*` API 调度主动搭话。
- 状态、锁和持久化加载器不存在新旧两份实现。
- 当前可用环境内的静态检查和定向直接回归通过；完整 pytest 环境阻断按上方记录保留，待有效 Python 3.11 + uv 环境复验。

## `main_logic` 人工回归报告

该 PR 修改了 `main_logic/`，必须人工 review。下表按 canonical owner 说明改动、必要性、前后表现和潜在回归：

| 文件 | 改动与必要性 | 前后表现 | 人工审核重点 |
|---|---|---|---|
| `contracts.py` | 集中命令、结果、reason/stage/action 契约。 | 旧 Router 导出继续可用，wire shape 不变。 | reason 到 stage/action 映射、HTTP status。 |
| `state.py` | 统一历史、计数、锁和持久化所有权。 | 旧 facade 与新模块引用同一状态；显式使用注入的 `memory_dir`。 | 根目录注入、原子读写、无双份状态。 |
| `decisions.py` | 迁移入口 gate、activity/source 选择等纯决策。 | 概率、优先级和 PASS 条件不变。 | gate 顺序、随机调用次数、source 权重。 |
| `generation.py` | 集中 Phase 1/2 解析、模型请求、stream 和输出保护。 | 模型 tier、Prompt、timeout、重试、vision/thinking、PASS 和去重语义不变。 | 消息 shape、重试异常集合、格式/BM25 regen、action note 清理边界。 |
| `delivery.py` | 集中投递提交、成功后记录和生命周期结束。 | WebSocket/TTS/plugin 的提交顺序与失败降级不变。 | SID 抢占、falsey websocket、异常传播和日志。 |
| `mini_game_invite.py` | 迁移邀请状态机，并将 hook 注册改为显式安装。 | 关键词、概率、冷却、按钮和 payload 不变；单独 import 无副作用。 | Router 启动是否必经安装、identity 去重、first-hit-wins。 |
| `music_recommendation.py` | 集中搜索 fallback、选择、播放完成和动态约束。 | 推荐概率、链接、播放中/冷却拦截和历史语义不变。 | fallback、完整播放仅清 music 标记、数据级锁。 |
| `break_reminders.py` | 迁移 anti-slack、休息提醒和组合邀请子流程。 | Router 注入与原短路顺序不变。 | 模型参数、短路返回、小游戏组合 payload。 |
| `content_logging.py` | 只保留本模块实现的 source 内容日志。 | 日志内容不变；音乐 helper 从其 canonical 模块导入。 | logger namespace、旧 facade 导出。 |
| `sources.py` | 新增来源抓取、并发收集和链接归一化。 | fetch 顺序、limit、timeout、异常降级和 result shape 不变。 | screenshot/avatar 处理、各来源 links 顺序、失败日志。 |
| `service.py` | 作为框架无关编排器串联 gate、source、Phase 1/2、投递和记录。 | HTTP/WS 由 Router 适配，业务退出与生命周期仍统一收口。 | 阶段顺序、抢占点、模型配置注入、最终 commit/record。 |
| `__init__.py` | 定义领域包并避免不必要的聚合副作用。 | 不新增业务行为。 | 不应在包 import 时触发 Router 或 hook 注册。 |

`app/` 与 `memory/` 在 6.1 收口中没有代码修改，因此本轮不新增这两个高风险模块的人工审核项。

## PR 切分与回滚

本阶段按四个文件互斥的 PR 同时提交，并要求按依赖顺序合并：

1. 状态与契约基础：`contracts.py`、`state.py`、`decisions.py` 及旧状态/来源路径兼容入口。
2. 独立领域能力：休息提醒、内容日志、小游戏邀请、音乐推荐及对应 Router 适配；依赖 PR 1。
3. 生成与来源处理：`generation.py`、`sources.py` 及旧解析路径兼容入口；依赖 PR 2。
4. Service 编排与 Router 瘦身：`delivery.py`、`service.py`、生命周期收口和薄 Router；依赖 PR 3。

四个分支从同一个上游 `main` 基线创建，每个 PR 只包含自己负责的文件，因此 diff 不重复。后置 PR 在前置依赖合并前保持 Draft；前置 PR 合并后，将下一个分支 rebase 到最新 `main`，完成回归后再转为 Ready。出现问题时优先回滚当前领域模块或恢复旧适配调用，不通过复制状态实现临时双写。

前端内部拆分继续独立排期，不属于上述四个 PR。

由于改动涉及 `main_logic/`，PR 描述必须按仓库规范填写非空“回归报告”，说明改动、必要性、前后表现和潜在回归点；若单个 PR 超过仓库文件数阈值，还需填写“不拆分理由”。

## 维护原则

- 不把主动搭话业务逻辑放进 `utils/`。
- 不引入新的顶层 `proactive_chat/` 包。
- 不在模块迁移中重调主动搭话策略。
- 不把 Prompt 文案迁出 `config/prompts/prompts_proactive.py`。
- 不把 HTTP 框架对象传入领域 helper 或 service。
- 不让领域层反向 import Router。
- 不跨模块直接读取或修改可变状态字典，优先使用窄查询和更新接口。
- 不在异步主动搭话链路中引入同步文件 I/O、同步 HTTP 或阻塞等待。
- 对外协议优先兼容；内部 re-export 允许过渡，但必须有清理条件。
- 新 source 或 gate 进入 `decisions.py`，新生成保护进入 `generation.py`；小游戏邀请和音乐推荐规则分别进入对应的具名子能力模块。
- 新增退出分支必须显式经过统一生命周期结束和投递清理。

## 重构前后对比

| 维度 | 重构前 | 重构后 |
|---|---|---|
| 主流程可读性 | Router 已按领域拆包，但主动搭话主流程仍集中在约 2933 行的 `proactive_chat_flow.py`。 | Router 只保留协议适配；service 串联具名阶段，不再内嵌 source/LLM helper。 |
| 职责边界 | Router 子模块仍同时承担 HTTP、领域状态、策略和生成编排。 | Router、领域逻辑、通用工具、Prompt、投递阶段和状态机各自归位。 |
| 状态所有权 | 历史和 source 模块之间存在对可变全局状态的直接引用。 | `state.py` 是唯一所有者；主流程显式传递持久化根目录，旧 facade 共享同一状态对象。 |
| 子能力规模 | 小游戏路由、状态、业务规则和 WebSocket payload 集中在同一 Router 文件。 | 小游戏、音乐、休息提醒、内容日志和投递阶段拥有具名领域模块，Router 只做协议适配。 |
| 测试方式 | 部分测试仍依赖 Router 内部函数或兼容门面。 | 领域规则直接单测，Router 保留协议、注入和兼容契约测试。 |
| 行为稳定性 | 大流程新增分支时容易遗漏 reason、stage、历史、计数或生命周期清理。 | 契约、状态、投递提交和生命周期结束均由明确边界统一处理，并以重构前 diff 回查意外变化。 |
| 前端兼容 | `static/app/app-proactive.js` 承担调度、采集、传输和兼容 API。 | 第一阶段保持不变；后续内部拆分仍由原文件导出 `window.*` API。 |
| 回滚成本 | 主流程移动容易牵动多个 Router 子模块和共享状态。 | 按 canonical owner 小步迁移，每个 PR 可独立回滚且不双写状态。 |

整体效果：用户侧行为和全部外部协议保持不变；维护侧从“在 Router 子模块和大流程中定位分支”转为“按主动搭话领域模块定位契约、状态、决策、来源、生成、具名子能力和编排”。
