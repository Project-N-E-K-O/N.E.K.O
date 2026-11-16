# 会话启停、跨模式重启、热切换

总览见 `docs/architecture/session-management.md`。代码主要在 `main_logic/core/lifecycle.py`、`session_lifecycle.py`、`asr_runtime.py`、`notify.py`，前端在 `static/app/app-state.js`、`app-buttons.js`、`app-websocket.js`、`app-audio-capture.js`。

改这一带的总原则：**核心路径不回退是验收线**。自动评审会沿着并发交错一轮轮往深处挖，多数成立但属于极端交错。每条先判「是不是常见路径上的新回退」，是才动，不是就记录不改：动 `start_session` 的核心收尾本身就有回归风险。这条判据只管「要不要为自动评审挖出来的极端交错去动核心收尾」。「热切换：final swap」一节的几条取消规则不在此列：违反它们的改动必须修，哪怕只在极端交错里才触发。「已接受不修的残留」也只指「跨模式启动撞车」一节列出的那几项。

## 跨模式启动撞车

场景：后端自己起了一个文本会话（主动搭话、问候要播 TTS），同时用户点了「开始语音」。前端有 15 秒启动超时（`FRONTEND_START_SESSION_TIMEOUT_SECONDS`）。

现行规则（`lifecycle.py` 的 in-flight 处理）：

- 只有 `user_initiated=True` 的请求才会「等在途启动落定，再重入起目标模式」。这个参数只有 `main_routers/websocket_router.py` 处理用户的 `start_session` 动作时传。后台的主动搭话 / 问候撞车时保持静默返回，不许顶掉用户的语音会话。
- 等待上限是 `CROSS_MODE_RESTART_WAIT_SECONDS`（前端超时的一半），给重启本身留余量。
- 区分「在途启动真的落定了」和「用户放弃了」用 `_user_session_abandon_epoch`。它只在用户侧的 `end_session`（`not by_server and reset_starting_count`）递增。⚠️ 不能用 `_audio_stream_epoch`：所有内部清理都会让它涨，会把还在等的语音启动误判成已放弃。
- 重入前校验：本次请求带进来的那把 websocket 仍连着（看参数里的 ws，不看 `self.websocket`，后者会被失败清理置空）；`self.websocket` 要么还是这把、要么是 `None`（是别的连接就说明浏览器重连了，不能拿旧 ws 去抢）。
- 重入时传 `_allow_cross_mode_restart=False`，递归深度封顶 1；重入前调 `reset_session_start_circuit()`，否则在途启动失败触发的熔断会让重入静默早退，前端又干等 15 秒。
- 失败路径的清理要保住失败任务自己的启动计数，但必须显式清掉待发输入，否则输入会漏到下一个会话。不要为此去改 `end_session` 的门控：重建路径同样的参数下需要保留输入回放。
- 前端按模式过滤：等着 `audio` 的启动收到 `text` 的 `session_started` / `session_failed` 时忽略（`_pendingSessionStartMode`）。

已接受不修的残留（都要改核心收尾，回归面大于收益）：在途启动贴着等待上限才落定时的晚到 ack；等待期间后台投递抢在刚起好的文本会话上；在途失败叠加浏览器重连的多重交错；请求落在「熔断已开、计数未掉」的窄窗。

## 同模式去重：补发的 ack 要带路由和请求 id

同模式的第二个启动请求不会真的再起一个，只补发一次 `session_started`。两条不变式：

1. **ack 带上已落定的麦克风路由**（`microphone_route`，`notify.py::send_session_started`）。路由裁决平时只走麦克风控制面的状态消息，有些路径一条状态都发不出来（比如第二个窗口抢了语音 lease，在途的 ASR 启动被作废，它的早退在所有状态发射点之前）。前端对这个字段**只置真、不清假**：非文本模式且路由是 `blocked` 才锁住，闩是刻意粘住的，只有用户再次点麦时才解。
2. **ack 带 `request_id`**。没有它，ack 是匿名的，有启动在等的窗口会把别人的同模式 ack 当成自己的。第二个窗口抢麦时语音 socket 被同步换成它的，在途启动的 ack 落到它头上，它按 `blocked` 直接放弃开麦，之后真正属于它的 ack 到来也救不回来。前端在占启动槽位时铸标识（按 owner 存，每个发送点只读自己那一个），只让标识匹配的 ack 收口；匹配规则在 `app-state.js` 的 `sessionStartNotificationAnswersPending` / `sessionStartNotificationIsRetired`。

去重路径上还有一步「先重决、后补 ack」（`asr_runtime.py::_rerun_route_for_deduped_start`）：

- 只在本请求是 `audio`、在途会话也是 `audio`、路由还是 `blocked` 时重跑。已落定的 native / independent 路由不能重跑，那会把健康的 provider 中途拆了重连。
- 判断在途会话的模式要用 `self.input_mode`，不能只看本请求：去重分支把「没有在途模式」也当成同模式匹配，音频请求可能撞上在途的**文本**启动，文本会话的路由本来就该一直是 `blocked`。
- 重跑前校验语音 lease 没在等待期间易主（用等待前的快照比）。
- 重跑前校验前端超时还剩得下一次连接预算（`ASR_CONNECT_TOTAL_BUDGET_SECONDS`）。不校验的话补发的 ack 会晚于前端放弃，前端超时触发的 `end_session` 会把真正起来的会话拆掉。
- 重跑之后再查一次 lease：易主了就给这个请求方单独回 `blocked`（`microphone_route_override`，只对定向那一份生效，不进广播），让它干净地收口并可重试。

前后端同步上线，这些字段不需要为旧客户端做兼容。

## 麦克风 lease 的分层

- lease 状态（谁持有、静音、焦点）只在 **PCM 入口**决定帧收不收，不参与路由编排的围栏。启动的围栏是「操作代数、连接 id、`blocked` 占位路由」。
- 前端只在 `session_started` 之后才把 owner 翻给后端。任何把 owner 塞进启动前置条件的改动都会打死冷启动。
- 音频启动时 lease 的持有者就是发起方（router 同步认领早于异步的 `start_session`）。`session_started` 要同时送到 lease 持有者，不能因为模式是文本以外就只发给「最新的显示连接」：启动要几秒，期间开第二个窗口会把 `self.websocket` 换走。

## 前端：语音启动槽位

前端的启动状态（resolver、rejecter、待定模式、超时句柄、语音启动 epoch）是并发启动共享的全局槽位。

- 四条启动 flow（麦克风按钮、文本发送、composer 文本、自动重启）的占用和释放都走 `claimSessionStart` / `releaseSessionStart` / `sessionStartIsCurrent`（`app-state.js`），token 就是 resolver 本身。带归属判断的站点只会拒绝清别人的槽位。
- **刻意保持无条件**的清理：`cancelPendingSessionStart`、`session_failed` 处理、服务端结束会话、断连清理、`stopMicCapture`。它们是「放弃一切待定启动」的全局杠杆，不要给它们加归属判断。
- ⚠️ `S.voiceSessionStartEpoch` 的语义是「最新的**语音**启动意图」。文本启动不许递增它：
  - 两处 stand-down 检查用「epoch 已不是我的」表示「取消优先于接管」，走不跑全局收尾的那一支。文本启动也递增的话，文本接管会被误判成取消，麦克风按钮不再交还。
  - `markVoiceSettingsPending`（`app-audio-capture.js`）把「下次语音启动生效」编码成 `epoch + 1`，文本启动递增会让一次文本发送吞掉待生效的语音路由变更。
- 开麦（`startMicCapture` / `startAudioWorklet`）要「全部在本次尝试的局部变量上构建，过了闸门再一次性发布到全局，失败只拆自己造的」。两次重叠的开麦如果拆的是全局对象，被抢占的那次会拆掉胜出那次的硬件，用户完全没麦。
- 测试：`tests/unit/test_voice_start_teardown_paths.py`（把 flow 里的判定函数逐字切出来，配真实的 `app-state.js` helper 在 node 里跑）、`tests/unit/test_mic_start_race.py`。测试桩的 `disconnect()` 之类不能是空实现，否则「该拆的没拆」测不出来。

## 会话启动时的配置读取

- async 路径上读配置用 `aget_core_config()` / `aget_model_api_config()`（`utils/config_manager/core_config.py`）。静态闸门 `tests/unit/test_config_read_off_event_loop.py` 扫后端目录，禁止 `async def`（含其中同步调用的嵌套闭包）直接调同步读。
  - ⚠️ 闸门是词法的：「另外定义的同步方法里读配置，再被 `async def` 调用」它看不见。新增这种 helper 时自己盯。
- `aget_model_api_config(core_config=...)` 可以复用调用方已有的快照，但**只在相邻时**。跨过可能挂起很久的 `await`（例如等记忆拉取）之后必须重读，否则期间用户保存的新凭证被整轮忽略。构造 LLM 客户端的地方读**一份**新鲜快照，对话模型和视觉模型共用它；两次独立读会撕裂成「旧对话配置加新视觉配置」。
- `get_core_config()` 是纯读。读路径里不许写盘，否则它变成读改写，放进线程后会顶掉设置页的保存。需要落盘的迁移放到启动期（例如 `migrate_openclaw_url_port`）。
- 进程拓扑：打包版默认三个服务合并在一个进程里共享事件循环；从源码运行默认是多进程（`launcher_core/runtime.py::_should_use_merged_mode`，`NEKO_MERGED` 可覆盖）。「一次阻塞拖慢三个子系统」只在合并模式成立，而用户实际跑的就是合并模式；在源码模式下测不出卡顿不代表没问题。

## 热切换：final swap

`lifecycle.py::_perform_final_swap_sequence` 以 `final_swap_task` 的形式运行。四条规则，各自对应一个出过的坑：

1. **步骤 1 等旧 listener 退出时，`except CancelledError` 只能吞旧 listener 的取消回波。** 外层对 `final_swap_task` 自己的取消（`_reset_preparation_state`、`end_session`）在同一个 `await` 点抛的也是 `CancelledError`。一起吞掉的后果：swap 活过了取消，追踪引用在 2 秒超时后被清空，僵尸 swap 之后无锁地把 `self.session` 换掉，覆盖并发 `start_session` 的赢家，或者让后续每次启动都输给一个毒化的会话。判据：`asyncio.current_task().cancelling() > 0` 就重新抛。
2. **promote 之前再查一次 `cancelling()`。** `asyncio.wait_for` 和某些 `session.close()` 实现，在外层取消恰好落在内层 `await` 完成之后时，会「正常返回」式地吞掉取消：`except` 不触发，但 `cancelling() > 0` 还在。
3. **promote 必须持锁并比较**：只有 `self.session is old_main_session` 才赋值，否则关掉新会话并返回，不回滚接管方的状态。和启动侧的强 CAS 对偶。
4. **`_reset_preparation_state` 不取消、不等待当前任务自己。** swap 的中止分支会调它；它如果取消了正在运行的自己，取消会在里面的等待点打回调用方的 `except` 块，之后所有清理代码都成了死代码。

测试要点（`tests/unit/test_hot_swap_cancellation.py`）：

- ⚠️ 测 swap 内的清理逻辑必须按生产拓扑跑：`mgr.final_swap_task = asyncio.create_task(...)`，并断言 `not task.cancelled()`。直接 `await` 协程是假绿，第 4 条就是这样漏掉的。
- 验证第 2 条的独立价值，要构造「取消已被吞、但 `cancelling() > 0`」的状态（在 `close` 里取消自己、让出一次、吞掉异常）。普通取消会被 promote 的加锁等待兜住，测不出区别。

热切换时摘走的待投递回调：队列条目**原地不动，promote 成功后才按对象身份移除**。不要「先摘走、中止时塞回」：窗口期里别的路径（语音投递成功、撤回、话题清扫）要移除的条目已经不在队列里，塞回会让它复活、重复播报。promote 之后会话还活着的失败不塞回（内容已经在它的上下文里）。

## `goodbye_silent` 的跨连接对账

后端 `LLMSessionManager.goodbye_silent`（请她离开后压住问候和主动搭话）跨 websocket 重连存活，清它只有两条路：收到 `goodbye_state:false`，或 `start_session`。前端的 goodbye 是纯内存的视觉状态，刷新页面就重置，后端不会跟着清。

- 对账不能放在 websocket 的 `onopen`：它不区分窗口；不挂模型管理器的聊天窗口永远判成「不在 goodbye」，盲发 `goodbye_state:false` 会误清别的窗口里真实的 goodbye。
- 唯一有权威判断的是 `static/app/app-auto-goodbye.js`（只在主页和具名角色页启动）：连接就绪时对账一次，条件是「全新页面（没有 `window.__nekoGoodbyeSilentState` 记忆）且当前不在 goodbye」。
- 同一角色后端只认一个活的 websocket（新连接会顶掉旧连接），所以「同角色两个稳定窗口、一个还在 goodbye」不可达；评审意见说「对账会清掉另一个窗口的 goodbye」时按此驳回。

测试：`tests/unit/test_app_auto_goodbye_phase1.py`。
