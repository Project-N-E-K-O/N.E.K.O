# 主动搭话的门控链

主流程在 `main_logic/proactive_chat/service.py::handle_proactive_chat`，纯决策函数在 `decisions.py`，生成和输出保护在 `generation.py`，提交和记账在 `delivery.py`。前端按间隔调 `POST /api/proactive_chat`，后端决定这一轮说不说。下面按执行顺序列出每道闸、它挡的是什么、对应的 `reason_code`。

## 入口闸（还没占位）

| 顺序 | 闸 | 含义 | 结果 |
|---|---|---|---|
| 1 | `_decide_manager_entry_guard` | 角色不存在；或会话处于告别后的静默（`is_goodbye_silent`） | 404 / `PASS_DISABLED` |
| 2 | `_decide_game_route_entry_guard` | 游戏路由正在占用这个角色。检查本身抛错也按「占用」处理（fail-closed） | `PASS_ROUTE_ACTIVE` |
| 3 | 语音快路径 | 请求带 `voice_mode` 且当前是实时语音会话：不走 Phase 1/2，不取活动快照，只读 `can_start_proactive`，然后 `trigger_voice_proactive_nudge` | 409 `PASS_BUSY` / `CHAT_DELIVERED` |
| 4 | `try_start_proactive` | 文字模式的原子占位：检查「AI 没在回复、没有另一轮主动搭话」并把状态机推到 PHASE1，在同一把锁里完成 | 409 `PASS_BUSY` |

语音快路径不经过活动追踪器，所以小游戏邀请的「用户有没有回应」要在这条路径里单独推进，并且用 `last_user_message_time`（真实的用户发言），不能用 `last_user_activity_time`——后者会被麦克风收到的 TTS 回声刷新，导致邀请刚念完就被判成「用户已回应」。

## 活动闸（已占位，还没花模型调用）

占位之后先读隐私模式：开着就不取活动快照，`activity_snapshot = None`，后面所有依赖快照的闸自动失效，等于「没有追踪器时的不限制策略」。读设置失败按隐私模式开启处理。

| 顺序 | 闸 | 含义 | 结果 |
|---|---|---|---|
| 5 | `_decide_closed_activity_gate` | 快照的 `propensity == "closed"`（前台是密码管理器、网银这类 `private` 状态）。什么都不做：不抓来源、不拼 prompt、不调模型。有未收尾话题也不例外 | `PASS_PRIVACY` |
| 6 | `_decide_activity_schedule` | `restricted_screen_only`（游戏、专注工作）：通知前端改用固定间隔，后端在调模型前随机 sleep 一段做抖动。有必发提醒（见下）时跳过 sleep，但仍要通知前端用固定间隔 | 不退出 |
| 7 | 必发提醒短路 | `anti_slack_pending` 优先于 `work_break_pending`。跳过 Phase 1，走 `break_reminders.py::_deliver_break_reminder_via_llm` | `CHAT_DELIVERED` / `PASS_DUPLICATE` / `DELIVERY_PREEMPTED` |
| 8 | `_decide_probabilistic_activity_gate` | 按快照的 `skip_probability` 掷骰。有未收尾话题时不掷 | `PASS_THROTTLED` |
| 9 | `_select_source_modes` | `restricted_screen_only` 下把来源收紧到只剩 `vision`；前端这轮没开 `vision` 就退出，除非有未收尾话题（允许纯文字跟进） | `PASS_RESTRICTED_SCREEN_ONLY` |
| 10 | 邀请短路（小游戏、小剧场） | 独立掷骰，命中就走邀请自己的投递流程，跳过来源抓取和 Phase 1/2。快照为 `None` 时不发（判断不了用户是不是在工作） | 邀请自己的结果 |
| 11 | `_decide_empty_source_gate` | 所有来源开关都关，邀请也没命中，又没有未收尾话题 | `PASS_SOURCE_EMPTY` |

请求体里 `enabled_modes` 缺失和显式空数组含义不同：缺失是旧前端，按其它字段推断；显式 `[]` 是「来源全关」，不能回落到首页热点。

## 来源、Phase 1、Phase 2

| 顺序 | 步骤 | 可能的退出 |
|---|---|---|
| 12 | `_collect_sources_under_vision_consent` 并发抓来源。视觉授权被撤回时，采集前去掉屏幕/窗口来源，采集后丢掉已取回的结果 | 来源全被授权撤掉 → `PASS_PRIVACY`；全部抓取失败 → 500 `ERROR_SOURCE_FETCH_FAILED`；有未收尾话题时即使没来源也继续 |
| 13 | 取记忆上下文、近期搭话记录、反思与回访话题、模型配置 | 配置缺失 → `ERROR_INTERNAL` |
| 14 | Phase 1：一次模型调用同时做网页筛选和音乐/表情包关键词 | 没有任何值得说的、没有截图、没有未收尾话题、没有到期的承诺 → `PASS_MODEL_PASS` |
| 15 | 向前端要一张最新截图（见 `screenshot.md`） | — |
| 16 | `prepare_proactive_delivery(min_idle_secs=10)`：告别静默、用户最近 10 秒内有活动、实时语音会话在跑、WebSocket 不在线，任一成立就不说；通过后生成新的 `current_speech_id` 并 `PROACTIVE_CLAIM` | `PASS_ACTIVITY_BUSY` |
| 17 | Phase 2 流式生成，边生成边送 TTS | 模型输出 `[PASS]`、空输出、缺来源标签且自救失败 → `PASS_MODEL_PASS` / `PASS_GENERATION_EMPTY` |
| 18 | `_guard_phase2_output`：用户禁令 → 字面查重 → 未回应长窗 → BM25（详见 `user-directives.md`、`anti-repeat.md`），之后是音乐的数据级锁（正在播放时模型又推歌 → 取消；冷却期推歌 → 降级成普通聊天） | `PASS_USER_DIRECTIVE` / `PASS_DUPLICATE` / `PASS_MODEL_PASS` |
| 19 | 生成期间视觉授权被撤回，而这轮用了屏幕或窗口内容 | `PASS_PRIVACY` |
| 20 | `_commit_proactive_delivery` → `finish_proactive_delivery` | 归属检查不过 → `DELIVERY_PREEMPTED` |
| 21 | `_record_committed_delivery`：搭话历史、素材历史、计数、来源衰减历史、已提及的反思/承诺 | `CHAT_DELIVERED` |

所有退出都经过 `_end_proactive`（`ProactiveLifecycle.finalize`），它负责 `PROACTIVE_DONE`、凝神冷却和下一轮调度提示。

## 抢占和轮次归属

用户随时可能开口。主动搭话这一轮在任何时刻都可能已经「不归自己」了，判断归属有三个独立的量，缺一个都有漏洞：

- **`current_speech_id`**：用户发消息会换掉它。只查它不够——会话重启会让这一轮作废，但 `current_speech_id` 不变。
- **`proactive_round_id`**（服务层在占位时生成，同时用作截图请求 id）：会话重启、清场时作废。`ProactiveMixin._proactive_round_owned` 同时比对管理器和状态机上的两份。
- **`last_user_engagement_time`**：用户只点了界面、没发消息时，`current_speech_id` 不变，只有这个时间戳会前进。

做法：

- 服务层每跨一个 `await`（抓来源、取记忆、Phase 1、要截图、Phase 2）就查一次 `_round_preempted()`。
- `prepare_proactive_delivery` 在生成新 `current_speech_id` 之前、拿锁之后各查一次抢占；用户已经接管还去写 `current_speech_id`，会把用户那一轮的回复和 TTS 记到错误的 id 上。
- `feed_tts_chunk`、`finish_proactive_delivery` 都带三个期望值，在锁内和发布边界各查一次。
- 被拦下后清理 TTS 之前先查 `_proactive_turn_still_owned`：用户已接管时再调 `handle_new_message()` 会把用户正在播的回复一起清掉。
- 测试里用窄替身代替管理器时，状态替身要有真实的 `proactive_sid` / `proactive_round_id` 字段；`MagicMock` 自动生成的属性会让归属判断恒为「不持有」。

## 休息提醒、邀请、语音提醒不走完整出口

这三条短路分支不经过 `_guard_phase2_output`：

- 休息提醒只做「未回应长窗」检测（`break_reminders.py::_score_unanswered_break_reminder`），不做 BM25，不查用户禁令。
- 邀请走自己的投递流程；语音提醒由实时模型自己说。两者都不查用户禁令（`_proactive_directive_hits` 只在 `generation.py` 的 Phase 2 出口被调用）。

所以「用户禁令覆盖所有主动发声」目前不成立（#3013）。给这几条分支加新逻辑时，轮次归属（`round_id`）要和主路径一样传下去。

## 旁听检查和外部意图信号

一轮对话结束后，要不要检查「她是不是答应了却没办」由 `main_logic/cross_server.py` 在 `turn end` 时决定，只有同时满足这些条件才发检查请求：这一轮有用户输入、结束信号不是 `turn end agent_callback`、这一轮没有通过工具办事、最新的用户消息不是头像拖放。

- 主动搭话轮没有用户输入，所以不触发检查。这是有意的：没有新的用户输入，就不花模型调用去判断要不要办事。
- `finish_proactive_delivery` 正常发 `turn end`。只有投递过程中用户已经开始新一轮时才改发 `turn end agent_callback`，避免用主动搭话的结束去检查用户那一轮（那一轮通过工具办过的事会被再派一次）。
- 工具回调的收尾（`main_logic/core/turn.py::handle_proactive_complete`）发的也是 `turn end agent_callback`。

外部意图信号 `external_intent` 是检查前的一道免费预筛：

- 来源：读用户情绪的小模型调用顺带输出（`config/prompts/prompts_emotion.py`），0 到 1。语义是「这一轮多大程度上需要外部能力」：明确要求对外操作，或者需要外部、实时、超出已知范围的信息才能回答（只限这类疑问句，普通常识问题不算）。纯推理就能解的难题归 `complexity`，这里取低。改这段提示词要八种语言一起改，并且别把范围放宽到一般疑问句。
- 读取：`main_logic/activity/master_emotion.py::gate_signal_for` 返回 `max(external_intent, complexity)`，并且只在这份读数确实来自本轮这句用户文本时才返回；读数过期、文本对不上、带附件的轮都返回 `None`。
- 消费：`nekoclaw/host/decision.py::looks_like_a_task`。信号是 `None` 一律放行；只有信号已知且低于 `TURN_CHECK_INTENT_THRESHOLD`、用户的话里又没有命中任何插件关键词时，才省掉这次检查。

⚠️ 这个信号只能用来省调用，不能变成会挡住请求的条件。任何读不到、对不上的情况都必须落到「放行」。
