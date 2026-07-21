# TTS 管线

N.E.K.O. 有两条语音输出路径：`OmniRealtimeClient` 的 provider 原生音频，以及项目的外部 TTS runtime。本页描述的队列/worker 管线是后者，并非所有语音会话都会使用。

## 路径选择

`LLMSessionManager._resolve_session_use_tts()` 决定是否启动外部 runtime。

| 场景 | 语音路径 |
|---|---|
| 文本会话 | 外部 TTS runtime |
| 语音会话 + provider 支持的原生音色 | 实时 provider 原生音频 |
| 语音会话 + 自定义/克隆音色或指定外部 provider | 外部 TTS runtime |
| 使用免费实时服务的直播路由 | 服务端原生音频 |
| `DISABLE_TTS` | Dummy worker，不生成语音 |

音色路由同时依据结构化的 source/provider/reference、当前模型配置和已保存的克隆元数据。同一个 voice ID 在不同 provider 之间并不全局唯一。

## 外部 runtime

```text
LLM 文本增量
    │ 按 provider 类别规范化 / 剥离文本
    ▼
线程安全请求 Queue
    │ (speech_id, text) / 控制哨兵
    ▼
provider worker 线程
    │ provider 协议 + 源采样率转换
    ▼
线程安全响应 Queue ──> 异步响应 handler
                                  │
                                  ├─ JSON {type: "audio_chunk", speech_id}
                                  └─ 随后的 WebSocket 二进制 PCM 帧
```

每个 `LLMSessionManager` 拥有自己的队列和 daemon 线程。尽管 provider 网络协议不同，worker 都遵循 `(request_queue, response_queue, api_key, voice_id)` 调用契约。

Worker 大致分两类：

- `ws_bistream`：在长连接 WebSocket 上双向流式传输文本片段和音频，例如 Qwen、Step、CosyVoice。文本片段跳过 CJK 空白规范化，避免客户端缓冲干扰 provider 节奏。
- `http_sentence`：把清洗后的文本切句并发起合成请求，例如 OpenAI、Gemini、MiniMax、MiMo、Doubao 等。

两类路径都会从朗读文本中剥离 Markdown 标记和括号内舞台指示。

## Provider 分派

`main_logic/tts_client.get_tts_worker()` 先按优先级向 `utils/tts/provider_registry.py` 查询第一个命中的特殊 provider，再降级到 native/core 默认值。

当前注册的特殊路由包括：

| 优先级 | Provider | 选择条件 |
|---:|---|---|
| 10 | GPT-SoVITS | 已启用的本地自定义 TTS |
| 20 | vLLM-Omni | 显式端点/模型选择 |
| 30 | MiniMax | 克隆元数据 |
| 40 | ElevenLabs | 克隆或设计音色元数据 |
| 50 | CosyVoice | 克隆元数据 |
| 60 | MiMo | 已选预设音色或克隆元数据 |
| 65 | Doubao TTS | 克隆元数据 |

若特殊路由均未命中，core provider 默认值覆盖 Qwen、免费服务、Step、CogTTS/GLM、Gemini、OpenAI 和 Grok。不支持的原生组合会降级到 dummy worker，而不会静默调用无关 provider。

选择与凭证同时解析。provider 特定的 API key override 可防止 worker 错误继承另一个 TTS 配置槽的凭证。

## 流式处理与完成

LLM 增量以 `speech_id` 标记。Provider worker 未就绪时，文本保存在 `tts_pending_chunks`。Worker 发送 `__ready__` 后，异步 handler 按顺序刷新待处理文本。

控制项含义不同：

| 项 | 含义 |
|---|---|
| `(speech_id, text)` | 为当前话语合成文本 |
| `(None, None)` | 完成并刷新当前话语，worker 保持存活 |
| `("__interrupt__", None)` | 停止当前合成并静音迟到的 provider 回调 |
| `("__shutdown__", None)` | 退出 worker 线程 |

Done 标记会延迟到 worker 已就绪且待处理文本已刷新。Speech-ID 校验可防止旧轮次迟到的 done 标记结束新轮次 TTS。

## 中断

新用户活动抢占当前语音时，由 session/turn 处理路径发起中断，而不是依赖通用 provider `on_interrupt` 回调。

`_clear_tts_pipeline()` 会：

1. 清空已排队的响应音频；
2. 向存活 worker 发送 `__interrupt__`；
3. 重置文本 normalizer 和 stripper 状态；
4. 短暂等待 worker 静音回调；
5. 再清空迟到音频和待处理文本。

前端还会收到包含被中断 `speech_id` 的用户活动数据，从而停止正确的话语。中断以 worker 是否存活为闸门，而不是 `use_tts`，因为 mirror-speech 功能可能在原生音频会话中仍保留外部 worker。

## 音频契约与背压

Worker 在写入响应队列前，把 provider 输出转换成 48,000 Hz、单声道、有符号 16-bit little-endian PCM。很多 provider 的源为 24 kHz，但 GPT-SoVITS 等可配置服务可能使用不同源采样率；正确的重采样器由各 worker 管理。

浏览器先收到 `audio_chunk` JSON 头，再收到原始二进制音频。`speech_id` 位于头中，不在 PCM 帧内。

独立的麦克风输入队列上限为 300 条，满时丢弃最旧条目。该队列属于 session 输入流，不应与 TTS 请求/响应队列混淆。

## 错误恢复

Worker 通过响应队列报告结构化的 ready、reconnecting、warning 和 error 消息。Runtime 会分类凭证拒绝、限流、配额、策略阻断和连接失败等常见错误。

可重试错误最初静默重试，多次失败后通知前端；不可重试 code 立即报告。延迟 respawn 会校验预期 session 和 TTS 模式，避免为已经变化的会话复活 worker。

## 音色创建与试听

音色管理独立于流式合成：

| 端点 | 用途 |
|---|---|
| `POST /api/characters/voice_clone` | Multipart 克隆流程 |
| `POST /api/characters/voice_clone_direct` | 直接/provider 特定克隆流程 |
| `POST /api/characters/voice_design_preview` | ElevenLabs 音色设计预览 |
| `POST /api/characters/voice_design_create` | 保存设计音色 |
| `GET /api/characters/voices` | 当前有效音色目录 |
| `GET /api/characters/voice_preview` | 按 provider 试听 |

克隆端点并非只支持 DashScope。它为 CosyVoice 国内/国际、MiniMax 国内/国际、ElevenLabs、MiMo、vLLM-Omni 和 Doubao TTS 提供对偶的显式分支。部分 provider 返回远端 voice ID，另一些在本地保存参考音频并在每次合成时内联。

## 实现映射

| 关注点 | 文件 |
|---|---|
| 路径选择、队列、中断、投递 | `main_logic/core/tts_runtime.py` |
| Provider 分派和注册 | `main_logic/tts_client/__init__.py` |
| Provider worker | `main_logic/tts_client/workers/` |
| 分派元数据 | `utils/tts/provider_registry.py` |
| 原生音色路由 | `utils/tts/native_voice_registry.py` |
| 音色来源存储 | `utils/voice_config.py` |
| 克隆和设计端点 | `main_routers/characters_router/voice_cloning.py` |
| 试听和音色目录 | `main_routers/characters_router/voice_preview.py` |
