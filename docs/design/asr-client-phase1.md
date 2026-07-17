# Phase 1 ASR client

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

Phase 1 联调前需要在进程环境中显式设置：

```text
ASR_PROVIDER=dummy
```

dummy 不进入持久化 Core 配置和设置 UI，也不会成为未实现 Core 的自动 fallback。

## 已冻结的行为

- 生产 ASR 跟随 `core_type` 路由；一个 Session 只使用一个 worker，不跨供应商 fallback。
- 默认 `endpointing_mode="manual"`。该模式下 `signal_user_activity_end()` 发送 `commit`；`provider` 模式下不发送 `commit`，只刷新本地 48 kHz 流式重采样器的尾部音频，最终断句仍由供应商 VAD 决定。
- 公共输入固定为单声道 PCM16LE，支持 16 kHz 和 48 kHz。公共层将 48 kHz 流式转换为 16 kHz；一个 Session 首包锁定输入采样率。
- 空音频块是 no-op；非空音频必须为偶数字节，单块最多一秒。
- 只有首个有效、非空 `final` 调用 `on_input_transcript()`。`partial`、重复 final、冲突 final 以及 clear/close 后到达的旧 final 都不进入业务回调。
- 内部事件用 `generation + buffer_epoch + utterance_id` 关联，不能按 transcript 文本去重。
- callback 串行执行；业务 callback 失败不破坏 provider receive loop。
- worker `error` 终止当前 Session，只报告一次连接错误，不自动重连。恢复时由调用方创建新 Session。
- `close()` 幂等；可预知的未知 Core、未实现 backend、blocked backend 和配置错误在 `create_asr_session()` 阶段同步失败。

## Phase 1 边界

本阶段只提供 `asr_client` 公共骨架、唯一路由表和 dummy worker，不接入真实 ASR 供应商，不修改小游戏、`game_router`、`websocket_router.py`、现有 `streaming.py`、`OmniRealtimeClient`、普通语音链路或生产开关。

本阶段也不实现 Smart Turn、VAD、RNNoise、声纹、节流、LLM 回复、TTS、工具调用、上下文事件或独立 ASR 云服务。后续真实服务通过新增 worker 实现相同的 request/response 合同，不改变上述公共调用方式。
