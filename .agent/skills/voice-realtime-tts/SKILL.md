---
name: voice-realtime-tts
description: 语音会话、realtime 客户端、外部 TTS、独立 ASR 这条链路上不显然的不变式、既定取舍和踩过的坑。改 main_logic/core/（会话启停、热切换、TTS 运行时、ASR 路由）、main_logic/omni_realtime_client/、main_logic/tts_client/、main_logic/asr_client/，或前端语音启动 / 音频播放代码之前读。
---

# 语音 / realtime / TTS / ASR

## 先读已有文档（这里不重复）

- `docs/architecture/tts-pipeline.md`：两条出声路径、队列与哨兵、dispatch 顺序、打断。
- `docs/architecture/session-management.md`：`LLMSessionManager` 的启动状态机、热切换、输入缓存。
- `docs/design/tts-voice-source-unification.md`：provider / 声音来源两个维度的合同。
- `docs/design/asr-client-phase1.md`、`docs/design/smart-turn-v3-runtime-contract.md`：独立 ASR 的公共接口和端点合同。
- `.agent/rules/neko-guide.md`「core API native voice 走 native_voice_registry」一节。
- `.agent/skills/tts-error-reporting/SKILL.md`：worker 报错必须走 `("__error__", ...)`。

下面只写这些文档里没有、读代码也不容易看出来的东西。

## 最容易再踩的规则

1. **用户语音永远赢，程序发起的请求排队。** `RealtimeResponseArbiter`（`main_logic/omni_realtime_client/_response_arbiter.py`）只串行化程序发起的 `response.create`（主动搭话、工具结果、`create_response`）。用户开口打断走裸 `response.cancel`，不进队列。别把这个契约写反。
2. **arbiter 卡死时默认拆连接；保住连接是环境变量开关，且有一串「不能保」的条件。** 改 `_escalate` / `_cannot_keep_the_connection` 前读 `references/realtime-arbiter-and-turns.md`。
3. ⚠️ **不要在用户回合中途往 realtime 会话塞上下文。** `append_context(lifetime='current_session')` 对没有 `_conversation_history` 的会话（所有 realtime 语音会话）会走 `prime_context`，在 Gemini 上等于另开一个 user turn 并丢掉整轮回复，用户说完话角色一个音都不发。语音侧只写下一会话的缓存（`lifetime='next_session'`）。
4. **`audio_done` 只能后端发给前端，宁可漏发不可早发。** 前端自己判断不了「后面还有没有音频」。发射点必须 `await` 在该 `speech_id` 最后一块音频之后，不许 `create_task` / `_fire_task`。
5. **语音模式下外部 TTS 接管出声，只认明确选择。** TTS 下拉框明确选中该家，或角色用的是它目录里的音色原名，才接管；「跟随辅助 API」这种默认值不接管。就绪与否问 provider 自己 resolve 的结果，不要只看有没有 API Key（本地自定义端点可以没有 Key）。
6. **GPT-SoVITS 的「被选中」和「用哪份配置」是两套判据。** worker 永远读 `tts_custom`；`_resolve_tts_worker_spec` 返回的 config 在没有自定义音色时是 `tts_default`。任何 GSV 可用性 / URL 预检都要对齐 worker 的数据源。
7. **TTS 卡顿先分清两类**：整段卡几秒是事件循环被阻塞；一字一顿是上游流式节奏碎。两类修法不同，见 `references/tts-routing-and-workers.md`。
8. **热切换的 final swap 不能吞掉对自己的取消，promote 必须持锁比较后再赋值。** 步骤里凡是 `except CancelledError` 的地方都要区分「旧 listener 的取消回波」和「外层取消了我」（`asyncio.current_task().cancelling() > 0` 就重新抛）。
9. ⚠️ **`asyncio.wait_for(task, t)` 不是超时保护。** 它超时后会取消 task 并继续等取消完成；`handle_messages` 卡在 `recv()` 时可能迟迟不响应取消，于是永远不抛 `TimeoutError`。持着会话锁时这就是全链路卡死。只想「等到期」用 `asyncio.wait({task}, timeout=...)`。对应的测试不要 monkeypatch `wait_for`，那正好把真实行为盖住。
10. **`lifecycle.py` 里新增分支，必须有一条真正跑到函数体末尾的用例。** 这个文件的护栏很多是读源码文本的断言，对「这段代码根本执行不了」不敏感。语音分支曾因分支内先读后赋值的局部变量整段不可用，静态护栏、lint 和自动评审都没发现；现在的可执行覆盖在 `tests/unit/test_session_llm_start_paths.py`。
11. **前端 `S.voiceSessionStartEpoch` 只能由语音启动递增。** 文本启动不许碰它：两处 stand-down 检查和「语音设置下次启动生效」都依赖「它等于最新的语音启动意图」。钉在 `tests/unit/test_voice_start_teardown_paths.py`。
12. **麦克风 lease 只在 PCM 入口门帧，不参与启动编排的围栏。** 前端在 `session_started` 之后才把 owner 翻给后端，把 owner 塞进 start 的前置条件会打死冷启动。
13. **realtime 语音的路由归属用 last-write-wins，不要再换启发式。** `_note_input_route_identity_frame` 的 docstring 写了试过的每一种和它怎么坏；自动评审会反复按「歧义时绑 None」的方向报，照已定结论驳回。
14. **独立 ASR 回合的环境画面抽样成开头 / 中间 / 结尾三张**（`MAX_MULTIMODAL_TURN_IMAGES`）。别给图片另外发明「张数预算、多余丢弃」。

## 排查入口

| 现象 | 先看 |
|---|---|
| 语音连接莫名断开 | Main 日志里搜 `response arbiter failing closed`。没有这行就不是 arbiter 拆的连接。 |
| 语音能识别、有转写，但没有回复 | 同上；再看 `response arbiter kept the transport` / `failing open`。需要事件级证据时开 `NEKO_REALTIME_WIRE_TRACE=1`，用 `scripts/analyze_realtime_wire_trace.py` 离线分析。 |
| 每轮播的是上一轮的语音 | 看控制台里 `response.done` 那行的 `audio_deltas=`（带转写，走 `print`，不在日志文件里）。转写完整而 `audio_deltas=0`，说明上游这一轮没发音频帧，是上游错位，不是客户端。 |
| 自定义音色最后半句晚一轮才播 | 外部 TTS 的收尾绑在 realtime 的 `response.done` 上；见软 flush 一节。 |
| 语音轮提前收尾、嘴型提前停 | `audio_done` 的发射顺序和前端的几道兜底。 |
| 点了语音后一直转圈到超时 | 跨模式启动撞车、去重补发 ack 带的 `microphone_route`、`request_id` 对不上。 |
| 日志里连着两条 `Handling interruption` | 同一次打断打了两处日志，不是两次打断。 |

## references 索引

- `references/tts-routing-and-workers.md`：改 TTS dispatch、加 provider、动 worker 的收尾 / 缓冲、查卡顿或尾句延迟时读。
- `references/realtime-arbiter-and-turns.md`：改 arbiter、`prime_context` / `create_response`、打断、路由归属，或给 arbiter 写测试时读。
- `references/session-start-and-hotswap.md`：改 `start_session`、热切换、会话启动时的配置读取、前端语音启动槽位、`goodbye_silent` 时读。
- `references/asr-multimodal-turn.md`：改独立 ASR 回合里的屏幕 / 摄像头帧处理时读。
