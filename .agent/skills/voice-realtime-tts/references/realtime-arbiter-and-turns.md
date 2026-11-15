# realtime 回合：arbiter、注入、打断、路由归属

代码在 `main_logic/omni_realtime_client/`：`_response_arbiter.py`（排队与生命周期）、`_responses.py`（`prime_context` / `create_response` / `prompt_ephemeral` / 外部 ASR 回合）、`_transport.py`（收发与事件处理）、`_gemini_support.py`、`_protocol_capabilities.py`（各路由的协议差异）、`_wire_trace.py`。

## arbiter 的契约

`RealtimeResponseArbiter` 是一个单消费者优先队列，管**每一个客户端显式发起的 `response.create`**。它发出去之后不放开车道，一直持有到 `response.done`（或终态错误 / 取消），这样第二个调用方不会在第一个的 `response.created` 到达之前看到「没人在说话」。

- **程序发起的请求排队**：`create_response`、主动搭话（`prompt_ephemeral`）、工具结果续答。
- **用户语音永远赢**：
  - 服务端 VAD 判定用户开口时，`handle_interruption` 发裸 `response.cancel`（`cancel_response()` 不带 `wait`），**完全不经过队列**。
  - 独立 ASR 的用户文本轮走 `prepare_external_voice_turn`：暂停派发（`pause_dispatch`）、取消在途的程序请求（`cancel_current`）、自己插队，完了 `resume_dispatch`。
  - `cancel_current` 的生产调用方只有这一处和 `cancel_response(wait=True)`。
- **主动搭话的忙闲闸门**是 `is_active_response()`，它同时看 `_is_responding` 和 arbiter 的 `is_busy`（排队中的票、在途的服务端响应、待确认的 VAD 响应都算忙）。只看 `_is_responding` 会漏掉后半边。
- arbiter 自己不读配置。策略（`fail_open`）、回调（`abort_transport`、`on_stuck_release`）、协议能力、trace 开关都由构造点注入；构造点有两处，`_client.py` 的主构造和 `_responses.py` 的懒构造，改参数两处都要改。它只 import 同包的 `_protocol_capabilities` 和 `_wire_trace`，后者刻意不 import 包内任何东西，保证 arbiter 不拖着传输栈也能导入。

## 卡死时：默认拆连接，逃生阀保连接

所有「这个生命周期到不了终态」的升级都收口在 `_escalate`。

- **默认**：`_tear_down_transport`，打一行 `response arbiter failing closed: ...`（带 current / owner 来源、队列深度、VAD 是否待定），然后标记连接丢失并让传输层断开。对系统其余部分，这和上游主动断线看起来一样，所以这行日志是区分两者的唯一依据。
- **逃生阀**：环境变量 `NEKO_REALTIME_ARBITER_FAIL_OPEN`（`1/true/yes/on`，其余都算关；读取在 `_shared.py::response_arbiter_fail_open_enabled`）。打开后升级改走 `_release_stuck_lifecycle`：放弃卡死的那一轮，保住连接，日志是 `response arbiter failing open, transport kept: ...`。默认路径的行为在开关关闭时不变。

逃生阀打开也**不保连接**的情况，集中在 `_cannot_keep_the_connection`，返回的字符串就是原因：

- 队列消费者自己正悬在一次传输写里（`_worker_send_in_flight`）。它是唯一消费者，中断标志解不开那个 `await`，保连接等于对外宣称恢复而实际永久卡住。不要去取消或重启 worker，那会在活连接上撕开写了一半的帧。
- 刚有一次传输写失败。
- 被放弃的响应已经发上线但没有 id（之后的事件没法归属）。
- 服务端 VAD 宣布了一个响应但 id 还没来。
- 有一个没人拥有、也没有 id 的响应正在跑。

`_release_stuck_lifecycle` 刻意比 `_mark_connection_lost` 窄：不清连接可用标志、不递增连接代数、不放开派发暂停（独立 ASR 的暂停要活过无关的卡死轮）、不清空队列。顺序上先通知宿主这一轮结束，再放开车道。

宿主回调 `_transport.py::_on_arbiter_stuck_release` 现在做的事**和 `response.done` 完全一样的三步、同样的顺序**。原因写在它的 docstring 里：多一种结束回合的方式就多一份要保持正确的东西，只清一个标志的做法每次都漏一块。改它之前读完那段 docstring，尤其是「按 id 只结束被放弃的那个响应」和「新一轮已经开始就什么都不碰，只让出 id」两条。

评审这类开关时的判据：某条意见描述的问题，**在开关关闭、完全不发生升级的默认路径上能不能复现**。能复现就是既有缺陷，和开关无关，另开 issue，不塞进同一个改动。给不可达路径加的防御，要先想清楚它在真的可达时是不是对的，不是就别加。

相关测试：`tests/unit/test_realtime_arbiter_fail_open.py`、`tests/unit/test_realtime_arbiter_escalation_scope.py`。

## 各路由的协议差异收在一处

`_protocol_capabilities.py::resolve_realtime_protocol_capabilities` 按路由给出一份不可变的能力描述，arbiter 只看能力，不看 provider 名字。未知路由一律走严格的 OpenAI 兼容生命周期。

免费档海外路由（`lanlan_app_gemini`）是差异最大的一条。前三条是实测观察，已固化成能力位；第四条是实测观察，没有对应的能力位：

- 不发 `response.created`，所以允许用「带 id 的内容事件」当作响应已开始的证据。
- 收到 `conversation.item.create` 就开始回答，不等 `response.create`（`responds_to_conversation_items`）。
- 函数调用事件上的 id 和终态事件的 id 对不上（`function_call_ids_match_terminal=False`）。
- 不认「只要文字」的输出模态，实时音频照发（外部 TTS 接管时这份音频被丢弃，见 `tts-routing-and-workers.md` 的尾句一节）。

OpenAI Realtime 正式版把会话项确认事件从 `conversation.item.created` 改名成了 `conversation.item.added`；只听旧名字的路由每一轮都会白等一次确认超时（#3350）。

症状「语音能识别、有转写，但没有回复」多半是这一类：某个事件被错记成 owner 的开始或永远等不到匹配的终态，车道被占住，后面的续答全在排队。排查用 wire trace，不要先假设「模型不会调工具」或「超时了」。

## wire trace

`NEKO_REALTIME_WIRE_TRACE=1` 打开后，Main 日志里出现 `[wire-trace] ` 和 `[arbiter-trace] ` 两种前缀的单行 JSON，只记事件类型、id、序号、计数、工具名、状态和大小，不记对话文本、转写、工具参数、音频和凭证。`scripts/analyze_realtime_wire_trace.py` 离线分析。

arbiter 的 logger 绑在 Main 服务上；看很旧的日志时，它的输出可能落在别的日志文件里。

## 往会话里塞上下文的三条通道

| 通道 | 行为 |
|---|---|
| `prime_context(text, skipped)` | `skipped=True` 或 Qwen：拼进 system instructions，走 `session.update`，不触发回复。`skipped=False`（GPT / GLM / Step）：经 `create_response` 注入一条一次性 user 消息并触发回复，文本不持久进 instructions。 |
| `create_response(text)` | 建一条 user 消息并触发回复，两步都进 arbiter。 |
| `prompt_ephemeral(...)` | 带闸门的主动搭话文本轮。 |

⚠️ **Gemini 没有 `session.update`。** `prime_context` 在 Gemini 上不管 `skipped` 是什么，都走 `send_client_content`，等于另开一个 user turn；`skipped=True` 时还会置 `_skip_until_next_response`，之后这一轮的文本、转写、音频、完成回调全部被挡掉。

后果：`main_logic/core/context_append.py::append_context` 对 `lifetime` 为 `current_session` / `session_family` 的请求，先看会话有没有 `_conversation_history`（只有文字会话 `OmniOfflineClient` 有）。有就直接追加到历史，安全；没有就调 `prime_context`。把这种调用放在**用户回合正中间**，Gemini 语音下每次都吞掉整轮回复。

- 既有调用点（游戏结算、热切换期）没出事，是因为那些时刻用户不在说话。
- 规则：想在用户回合期间给 realtime 会话塞东西，先问这条路会不会开新 turn 或置 skip 标志。只有文字会话能安全接收当前会话注入；语音侧只写 `next_session` 缓存，等会话重建或热切换时带上。
- 例：用户禁区话题的中途注入只写下一会话缓存，文字和语音都不注入活会话（`tests/unit/test_user_directives_midsession_inject.py`）。
- ⚠️ 单测把 `append_context` 整个 mock 掉时，realtime 分支零覆盖。这个坑测试抓不到，只能读 provider 实现。

Qwen 没有 `conversation.item.create`，所以 `prime_context(skipped=False)` 在 Qwen 上也走 `update_session`，永不进 arbiter 队列。

外部 ASR 的文本注入必须走「user 消息加裸 `response.create`」，不能用 per-response instructions：协议语义是替换人设。

## 给 arbiter 写测试的假绿坑

- **测 `prime_context` 进队列要用非 Qwen 模型**（Qwen 根本不进队列）。
- **不要给 Gemini 写 arbiter 用例**：Gemini 客户端从不经 arbiter 入队，这类用例必然假绿。
- `tests/unit/test_proactive_sm_integration.py` 的 `_make_voice_sess` 把 `is_active_response` 桩成只看 `_is_responding`，抹掉了 arbiter 忙闲那一半。断言对象是闸门本身时用 `_make_voice_sess_with_real_gate`。
- 端到端用例的家是 `tests/unit/test_realtime_arbiter_native_path.py`：用 `_wired_client()` 加 `_RecordingSocket` 驱动真的 `handle_messages`，不要手戳 `notify_*`。
- 评审「窄 `except` 漏了某类异常」之类的意见时，先查外层有没有兜底。热切换序列外层有总的异常处理，漏掉的异常不会泄漏资源，只是会给前端报一次内部错误；那两处窄 `except` 的意义是口径（良性放弃不惊动用户），不是防泄漏。

## 打断与「慢一轮」

症状：每一轮播出来的是上一轮的语音，停在轮边界上，直到换新连接（会话续期的热切换）才恢复。

判定方法（实测观察，判为上游侧错位）：

- `response.done` 有完整转写但 `audio_deltas=0`：计数和转写累积在同一个抑制闸门后面，转写完整就说明闸门开着，上游这一轮真的没发音频帧。客户端卡滞造不出这个 0。
- 错位之后「首个音频帧」到得比正常快很多：那是上一轮已合成、滞留在上游的音频被冲出来。
- 触发条件：服务端 VAD 把一句话切成两段，第一段提交后响应刚创建、首帧还没出，第二段开口触发打断，客户端发了 `response.cancel`。

为什么可以排除客户端：realtime 自带声音在后端是零缓冲直通（收到即重采样即发），前端收到即解码即调度，所有闸门都是**丢弃语义**。客户端出问题只会是「某一轮静音」，造不出「下一轮补播上一轮」。

## 路由归属（这句话属于哪个游戏路由）

`_transport.py::_note_input_route_identity_frame` 记录「开口之前最近一帧属于哪条路由」，用 last-write-wins。这是定论：

- 一个服务端 VAD 缓冲区可以真的同时装着两条路由的音频。归属黏一点就拒掉有效语音，新一点就误绑延迟的开口。任何单一标量启发式都不可能两边都满足。docstring 里列了试过的每一种、怎么坏、对应的测试名。
- 这类失败全是**静默**的：不匹配的转写在 `handle_input_transcript` 里被拒，位置在接管分发器之上，游戏什么都收不到，也不打日志。评估这里的任何改动先问「反方向会不会丢句」。
- 接受的残留：路由切换恰好落在上游开口检测的延迟窗口内，且说话轻到本地门没响，会误绑一次。
- 真要消掉残留，只有一条路：路由切换时隔离输入缓冲区。那属于实时音频子系统的改动，必须一并处理 Gemini（它的 `clear_audio_buffer` 是空操作，转写也不带 item id）。
- 歧义时绑 `None` 不是一律错，判据是「有没有证据」：有帧证据却因为看起来歧义而绑 `None`，是丢句；**零证据**时绑 `None` 是对的。

三条双向测试在 `tests/unit/runtime/test_realtime_host_turn_identity.py`。

新子系统顺手改到正在跑的语音路径时：新语义一律做成可选参数，不带新参数的既有调用方行为不变；是不是真回归，去翻内置游戏的前端调用点确认，不要只做静态推理。
