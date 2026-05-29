# 分支 `pr/1454` 相对 `main` 的改动详解（伴随项）

> 编写时间：2026-05-28
> 对比基准：本地 `main` 分支 == 本分支当前 HEAD 的祖先（`git log HEAD..main` 为空）
> 提交规模：8 个非合并提交，约 **+16067 / −65 行**，跨 89 个文件
>
> **PR 主旨：猫娘大乱斗（Neko Brawl Arena）— 详细见 [neko-brawl/pr-1454-overview.md](neko-brawl/pr-1454-overview.md)**
>
> 本文档只整理**伴随合入**的其它内容：**通用主动投递（Proactive Delivery）框架**、Minecraft Agent Prompt 微调、零星改动。

---

## 目录

- [一、提交清单](#一提交清单)
- [二、猫娘大乱斗（主旨，已独立成篇）](#二猫娘大乱斗主旨已独立成篇)
- [三、通用主动投递框架（Proactive Delivery）](#三通用主动投递框架proactive-delivery)
  - [3.1 设计目标与背景](#31-设计目标与背景)
  - [3.2 新增模块](#32-新增模块)
  - [3.3 `core.py` 接入点](#33-corepy-接入点)
  - [3.4 `app/main_server.py` 事件路由改造](#34-appmain_serverpy-事件路由改造)
  - [3.5 前端播放门控信号](#35-前端播放门控信号)
  - [3.6 Plugin SDK 字段扩展](#36-plugin-sdk-字段扩展)
  - [3.7 测试覆盖](#37-测试覆盖)
- [四、Minecraft Agent Prompt 调整](#四minecraft-agent-prompt-调整)
- [五、其它零星改动](#五其它零星改动)
- [六、风险与待办（伴随项）](#六风险与待办伴随项)

---

## 一、提交清单

| Commit | 标题 | 主题 |
|--------|------|------|
| `5dc24ce4` | NekoBattleArenaV2 | 大乱斗前后端初版骨架（+9620 行） |
| `9c2b35dd` | feat: update neko brawl arena prototype | 加入资源、`neko-brawl/` 子组件目录与音效模块 |
| `e9d359c7` | fix: make deck builder card collection scrollable | 组卡器卡池滚动 |
| `718f0b8f` | fix: limit forged cards to single deck copy | Forged 卡每副牌限 1 张 |
| `bd1331b1` | fix: refine forged card story prompt perspective | 微调故事生成提示词人称 |
| `b308b56a` | feat: expand neko brawl arena prototype | 扩展 Adventure Deck、结算 UI、教程面板、停服脚本 |
| `4ce797d4` | feat(proactive): 通用主动投递框架 — 前端播放门控 + 优先级排序 + 合并 (#1545) | 主动投递管理器（已合入 [#1545](https://github.com/Project-N-E-K-O/NEKO/pull/1545)） |
| `c84df695` | docs: add neko brawl exploration rules | 探索模式规则文档（1155 行） |

---

## 二、猫娘大乱斗（主旨，已独立成篇）

**这是本 PR 的主旨内容**，单独整理在 [neko-brawl/pr-1454-overview.md](neko-brawl/pr-1454-overview.md)，包含：

- 整体架构图（NEKO 主服务 ↔ 大乱斗后端 ↔ 大乱斗前端）
- 前端 `battle-arena/`（工程骨架、`BattleArena.jsx` 顶层壳、`neko-brawl/` 子组件、基础卡 C001-C013、40 张探索牌组、音效系统、资源、`localStorage` 持久化）
- 后端 `local_server/battle_arena_server/`（设计原则、6 条 HTTP 接口、当前猫娘解析、奇遇铸造机抽样、卡牌故事生成 prompt 设计、匹配队列与 Dummy 对手）
- 主进程桥接（`/battle-arena/avatar` 头像同步端点）
- 启动 / 停止脚本
- [exploration-rules.md](neko-brawl/exploration-rules.md) 25 章规则文档的索引
- 端到端数据流（铸造一张 Forged 卡的完整链路）
- 全部占位与待办

本文档下面只整理与大乱斗无关、但同样在本分支合入的伴随改动。

---

## 三、通用主动投递框架（Proactive Delivery）

提交 [`4ce797d4`](https://github.com/Project-N-E-K-O/NEKO/pull/1545)，已合入 PR #1545。

### 3.1 设计目标与背景

**问题**（语音模式尤为明显）：插件 `push_message(ai_behavior="respond")`、greeting、agent 任务结果等主动提示，被产生的速度远超猫娘能说出来的速度；旧门控以 realtime API 的 `response.done`（生成结束）为开闸信号，但此时前端音频缓冲区还在播放，于是「自己打断自己」。同时一条价值低的「状态摘要」和一条紧急的「你被打了」按同等优先级争抢出口。

**新前置层**坐在现有 `enqueue_agent_callback` + `trigger_agent_callbacks` **之前**，**不替换**底层投递管线，只决定 *哪条* / *何时* 释放：

| 维度 | 行为 |
|------|------|
| **优先级排序** | 数字越大越重要（沿用全仓约定：bilibili 礼物/SC=9，memo 提醒=8，study answer_evaluated=5）。未设置 = 0 = 最低 |
| **合并（coalesce）** | **opt-in**：仅当显式设置同一 `coalesce_key` 时才折叠为最新一条；未设置 → 唯一 key，永不合并（避免误丢 distinct 重要 cue） |
| **批量释放 + 播放门控** | 排队中的 cue 等到 *前端* `voice_play_end`（或 `text_end`）+ `min_gap_s` 后**一次性成批**释放，恢复「多条相邻 cue 进一个 LLM turn」的老语义 |
| **Min-gap pacing** | `min_gap_s` 内不释放，防 flood |
| **抢占 / 老化** | 开闸时按优先级取最新批；等待超过 `ttl_s` 的 cue 直接丢弃，不再说陈旧内容 |
| **看门狗** | `voice_play_end` 信号丢失（前端刷新/断线）时，`_max_play_s` 超时强制重置 playing flag |

### 3.2 新增模块

- `main_logic/proactive_delivery.py`（348 行）— `ProactiveDeliveryManager` 主类、`_QueuedCue` 队列单元、`effective_priority()` 标准化。
  - 全在 asyncio 事件循环内运行，所有公共方法同步、内部用 `create_task` 异步派发；单线程循环保证 await 间隔间的原子性，所以**没有锁**。
  - `submit()` → `_pump()` → `_run_deliver()` 的三段释放链。
  - 暴露：`submit()` / `on_playback_start` / `on_playback_end` / `on_text_start` / `on_text_end` / `reset_gate()` / `drain_pending()`。
- `main_logic/lifecycle_bus.py`（66 行）— `LifecycleEventBus`：进程内命名事件 pub/sub。
  - 信号：`voice_play_start` / `voice_play_end`（前端）、`text_start` / `text_end`（离线 client 文本边界）
  - Handler 错误隔离（一个订阅者抛错不影响其他）

### 3.3 `core.py` 接入点

`main_logic/core.py` +294 行，要点：

- `LLMSessionManager.__init__`：构造 `LifecycleEventBus(name=lanlan_name)` + `ProactiveDeliveryManager`，并把 `voice_play_*` / `text_*` 订阅到 manager。
- 新增 `submit_proactive_callback(callback, *, priority, coalesce_key)`：被动 cue 仍走老的 `enqueue_agent_callback`，**只有 `delivery_mode != "passive"`** 的主动 cue 进 manager。
- 新增 `_deliver_proactive_batch(callbacks)`：manager 开闸时回调，整批 enqueue + 单次 `trigger_agent_callbacks`。
- `start_session` 早期调用 `_reset_proactive_gate()`：清掉前一会话残留的 playback flag / manager 队列（前端中途断线时 `voice_play_end` 丢失场景）。**注意**：reset 必须放在熔断早退与「正在启动中」去重早退**之后**，否则会误清仍在播放的旧会话门控。
- `trigger_agent_callbacks` 的语音 gate 加入第三个条件 `self._is_voice_playing()`，与 `is_active_response()` / `phase != IDLE` 一起判断「真还在说」。
- 文本模式投递路径 `prompt_ephemeral` 前后 emit `text_start` / `text_end`，让 manager 对文本模式也施加 min-gap。
- **媒体处理**：proactive cue 携带的图片不在 `_handle_agent_event` 入口立即 `stream_image`，而是挂在 callback 的 `media_images` 字段；manager 释放时调用 `_stream_cb_media()` 在 inject 紧前面 stream 到当时**保证存在**的 session，避免出现「图片落在前一回合 / 没有 session」的错位。文本模式则把图片显式传给 `prompt_ephemeral(images=...)`，**不复用**用户的 `_pending_images` 暂存队列（否则会偷掉用户下一次发言的视觉上下文）。

### 3.4 `app/main_server.py` 事件路由改造

`app/main_server.py` 中 `_handle_agent_event` 的改造（除了 §2.3 头像端点外）：

- 主动 cue（`ai_behavior in {"respond", "read"}`）携带的 image media_parts：
  - `respond` + 有文本 → 推入 `deferred_proactive_images`（跟 callback 走 manager）
  - `read`（被动）或 image-only `respond`（没有文本驱动主动 turn） → 维持立即 `stream_image`
- 从 `event` 解析 `priority` + `coalesce_key`：
  - `int()` 转换失败（包含 JSON Infinity / NaN）默认回退 `0`，**不丢 callback**
  - `coalesce_key` 非字符串 → 空串 → manager 内自动生成唯一 key（不合并）
- `delivery_mode` 分叉：
  - `silent` → 跳过 LLM 通道（HUD 仍会触发）
  - `passive` → 原 `enqueue_agent_callback` 路径（不主动打断）
  - 其它（默认 `proactive`） → `mgr.submit_proactive_callback(..., priority=, coalesce_key=)`，移除原先「立即 `trigger_agent_callbacks` 异步任务」逻辑

### 3.5 前端播放门控信号

`static/app-audio-playback.js` 新增 `sendVoicePlaybackSignal(action, turnId)`：

- 在 `dispatchAssistantSpeechStart` → 发 `voice_play_start`
- 在 `dispatchAssistantSpeechEnd` → 发 `voice_play_end`
- 在取消/中断路径 → 也发 `voice_play_end`（语义：音频已经停下，开闸）

兼容 Electron WSProxy/IPC 桥接 socket：当 `readyState === undefined` 也尝试发送（用 try/catch 兜底）。

后端 WebSocket 路由 `main_routers/websocket_router.py` 接 16 行：把上述 action 派到对应 `mgr.lifecycle_bus.emit(...)`。

### 3.6 Plugin SDK 字段扩展

只动了 push 路径的字段定义，对插件作者透明：

| 文件 | 变更 |
|------|------|
| `plugin/sdk/shared/core/push_message_schema.py` | `push_message` 接受 `priority` / `coalesce_key` |
| `plugin/sdk/shared/core/context.py` | 透传 |
| `plugin/sdk/shared/core/types.py` | 类型签名 |
| `plugin/core/context.py` | 服务端 ctx 透传 |
| `plugin/_types/protocols.py` | Protocol 签名 |
| `plugin/server/messaging/proactive_bridge.py` | 把字段写进 agent event payload |

### 3.7 测试覆盖

- `tests/unit/test_proactive_delivery.py`（**189 行新增**）— 覆盖优先级、coalesce、min-gap、ttl、看门狗、`drain_pending` 顺序、`reset_gate` 不丢队列
- `tests/unit/test_passthrough_to_chat_bubble.py` +9 行 — 验证 `priority` / `coalesce_key` 跟随 ai_behavior
- `tests/unit/test_proactive_sm_integration.py` +9 行 — 状态机集成

---

## 四、Minecraft Agent Prompt 调整

`plugin/plugins/game_agent_minecraft/prompts.py` 改了两个核心提示模板（七语言全量同步：zh / en / ja / ko / ru / es / pt）：

1. **`IN_PROGRESS_FOLLOWUP`**（动作进行中的播报指引）
   - 增加约束：**当前动作还在进行时，不要派新任务、不要调用 `minecraft_task`**（这会打断正在做的事）。
   - 之前的版本鼓励「想换/打断就直接派新任务」，新版反过来：先描述、不抢方向盘。
2. **`KEEP_GOING_BODY`**（停下来时的「下一步选择」）
   - 加入「**如果主人刚刚交代了要做什么，就顺着他的意思来，别派一个会盖掉他要求的新动作**」。
   - 「派动作」从默认建议改为「只有真的有明显该做的事时才派」，优先用聊天确认。
   - 添加「**调用 `minecraft_task` 时不要把工具名说出来**」（防止把内部状态当对话播报）。

配套：`plugin/plugins/game_agent_minecraft/service.py` +23 / `plugin/plugins/game_agent_minecraft/__init__.py` +11 / `tests/unit/test_game_agent_minecraft_plugin.py` +5。

---

## 五、其它零星改动

- `main_logic/omni_offline_client.py` +38 / `main_logic/omni_realtime_client.py` +33 — 适配 lifecycle 信号 + 主动投递入口（接 `lifecycle_bus.emit`）。
- `.gitignore` +1 — 忽略大乱斗运行产物。

---

## 六、风险与待办（伴随项）

> 大乱斗自己的 TODO 见 [neko-brawl/pr-1454-overview.md §10](neko-brawl/pr-1454-overview.md#十已知占位与待办)。

### 主动投递

- **`LifecycleEventBus` 暂为进程内**：未来 PR 可能桥接到插件子进程，今天没有 main→plugin 事件下行通道，所以先 in-process。
- **看门狗 `_max_play_s` 默认 45s**：高于普通单段回复，但若用户回复非常长，看门狗会切回开闸；如果实际生产中出现切早问题，需要调大。

### Minecraft Agent

- 新提示词把「派新任务」的默认行为从「鼓励」改成「需要明显该做的事时再派」，需要观察一段时间确认是否会出现「主动性不足」的反例。

---

## 七、附：差异统计速查

```
+16067 / −65 行   89 个文件

主体分布（按本文档侧重）：
  ─ 大乱斗（PR 主旨，见独立文档）─────────────
  battle-arena/                              ~12500 行（含 lock 文件 2540）
  docs/neko-brawl/exploration-rules.md        ~1155 行
  local_server/battle_arena_server/           ~1500 行
  app/main_server.py (头像 CORS 端点)            ~30 行
  static/app-chat-avatar.js (syncAvatar)        ~30 行
  startup scripts (.bat / .ps1 / .py)          ~220 行
  ─ 伴随项 ─────────────────────────────────
  main_logic/proactive_delivery.py             ~350 行
  main_logic/core.py                           ~360 行（净增）
  main_logic/lifecycle_bus.py                   ~70 行
  app/main_server.py (proactive 事件路由)        ~76 行
  static/app-audio-playback.js (playback 信号)  ~25 行
  plugin/sdk + plugin/_types + plugin/core        ~16 行
  plugin/plugins/game_agent_minecraft/*         ~60 行
  tests/unit/test_proactive_delivery.py        ~190 行
```

Git 上 `main` 没有任何 HEAD 不包含的提交（`git log HEAD..main` 为空）—— 这只说明本分支**未落后** `main`，并不代表可以直接合并：本 PR 仍是标记「(Don't Merge)」的协作草稿分支，是否合并、何时合并由维护者决定。
