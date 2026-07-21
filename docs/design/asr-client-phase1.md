# Phase 1/2 ASR client

Phase 1 冻结一条只负责“实时 PCM 输入、整轮文本输出”的公共链路：

```text
玩家麦克风 PCM
  -> RealtimeAsrSession
  -> Core 对应的 ASR worker
  -> final 文本
  -> on_input_transcript(text)
```

实现按现有 `tts_client` 的职责分层组织为公共入口、公共基础层、唯一注册表和 workers。ASR 本身使用 asyncio 长连接；一次 `commit` 只结束当前 utterance，不关闭 Session。

## 公共接口

`main_logic.asr_client` 只稳定导出：

- `AsrSessionConfig`
- `RealtimeAsrSession`
- `create_asr_session`

worker 解析器是包内实现。调用方不得直接实例化 provider worker，也不得依赖内部 command/event 类型。

```python
from main_logic.asr_client import AsrSessionConfig, create_asr_session


async def on_transcript(text: str) -> None:
    print(text)


async def on_error(message: str) -> None:
    print(message)


session = create_asr_session(
    core_type="qwen",
    config=AsrSessionConfig(
        language="zh-CN",
        input_sample_rate_hz=48_000,
        endpointing_mode="manual",
    ),
    on_input_transcript=on_transcript,
    on_connection_error=on_error,
)

await session.connect()
await session.stream_audio(pcm16le_chunk, sample_rate_hz=48_000)
await session.signal_user_activity_end()
await session.close()
```

仅联调公共骨架时，可在进程环境中显式设置：

```text
ASR_PROVIDER=dummy
```

dummy 不进入持久化 Core 配置和设置 UI，也不会成为未实现 Core 的自动 fallback。

## 已冻结的行为

- 生产 ASR 跟随 `core_type` 路由；一个 Session 只使用一个 worker，不跨供应商 fallback。
- 公共断句语义只有 `manual` 与 `provider`。`manual` 下 `signal_user_activity_end()` 发送 `commit`；`provider` 下不发送 `commit`，只刷新本地 48 kHz 流式重采样器尾部，最终断句由供应商决定。`server_vad`、`endpointing` 等厂商字段只存在于 worker 内部。
- 默认模式跟随 Core 路由：`qwen`、`qwen_intl`、`step`、`grok` 使用 `provider`；`glm`、`gemini` 使用 `manual` 并由 Smart Turn 切分。Soniox 区域优选路由同样使用 `provider` 和自身 `<end>`。当前 OpenAI `gpt-realtime-whisper` 只支持手动 commit、没有 Provider turn detection，因此生产路由保持 blocked，不能用 Smart Turn 伪装成流式 Provider 断句。
- `endpointing_mode` 在 Session 创建时冻结，不能通过 `update_session()` 动态切换。
- 公共输入固定为单声道 PCM16LE，支持 16 kHz 和 48 kHz。公共层将 48 kHz 流式转换为 16 kHz；一个 Session 首包锁定输入采样率。
- 空音频块是 no-op；非空音频必须为偶数字节，单块最多一秒。
- 每个 utterance 只有首个有效、非空 `final` 调用 `on_input_transcript()`。即使供应商乱序返回多个 final，业务回调仍按 commit/语音开始顺序交付。`partial`、重复 final、冲突 final 以及 clear/close 后到达的旧 final 都不进入业务回调。
- 内部事件用 `generation + buffer_epoch + utterance_id` 关联，不能按 transcript 文本去重。
- callback 串行执行；业务 callback 失败不破坏 provider receive loop。
- worker `error` 终止当前 Session，只报告一次连接错误，不自动重连。恢复时由调用方创建新 Session。
- `close()` 幂等；可预知的未知 Core、未实现 backend、blocked backend 和配置错误在 `create_asr_session()` 阶段同步失败。

## 阶段边界与当前集成

以上接口与冻结行为描述 Phase 1/2 建立的公共契约。当前 Phase 3 在该契约上提供唯一路由表、dummy worker、Qwen/OpenAI/Step/Grok WSS worker、GLM/Gemini 分段 ASR worker 和 Soniox 流式 worker。Step 在完成凭据联调和 WSS smoke 前保持 `blocked_credentials`；Grok 已通过有效凭据下的单轮、多轮和重连 WSS 验收，registry 标记为 `implemented`；GLM/Gemini 由会话级 Voice Turn Adapter 为需要 Smart Turn 的路由提供断句，Soniox 则以供应商 `<end>` 为权威终点。

Phase 1/2 原本不修改小游戏、`game_router`、`websocket_router.py`、现有 `streaming.py`、`OmniRealtimeClient`、普通语音链路或生产开关；这是历史阶段边界，不是当前集成状态。Phase 3 已接入独立 ASR 会话生命周期和 Realtime Arbiter，并将有效 final 通过既有 Omni 文本入口注入一次、请求一次响应；小游戏与 `game_router` 仍不在本阶段范围内。

Phase 3 已接入独立 ASR 云服务，以及用于 GLM/Gemini 分段 ASR 的 Smart Turn；Silero/RNNoise 检测能力只负责无人说话时的本地节流和智能启停，不取得流式 ASR 的逻辑断句权。声纹和跨会话全局节流仍不在本阶段，且 ASR Session 本身不持有 LLM 回复、TTS、工具调用或产品路由。后续真实服务继续通过新增 worker 实现相同的 request/response 合同，不改变上述公共调用方式。
