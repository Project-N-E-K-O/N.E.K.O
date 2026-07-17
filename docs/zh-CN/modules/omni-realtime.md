# Realtime Client

**Package：** `main_logic/omni_realtime_client/`

`OmniRealtimeClient` 管理由 `LLMSessionManager` 为音频输入选择的原生音频对话路径。该类由 transport、response、audio、media/Gemini 和 tool mixin 组成，并通过稳定的 `main_logic.omni_realtime_client` 路径导入。

## Provider 传输

| Provider 路由 | 传输行为 |
|---|---|
| Qwen | DashScope realtime WebSocket 事件 |
| OpenAI / GPT | OpenAI realtime WebSocket 事件；上行音频转换为 24 kHz |
| Step | Step realtime WebSocket 事件 |
| GLM | 智谱 realtime WebSocket 事件 |
| Grok | Realtime WebSocket 事件路径 |
| Gemini | Google GenAI SDK live session，不走原始 WebSocket 实现 |
| 免费路由 | 取决于 endpoint：Gemini proxy/live-stream 或 Step 兼容行为 |

客户端依据 `api_type`、model 和 endpoint 配置选择一个分支。连接失败会暴露给会话管理器，不会静默创建 `OmniOfflineClient`。

## 公开生命周期

| 方法 | 契约 |
|---|---|
| `connect(instructions, ...)` | 打开所选 provider 会话，配置轮次检测、音频、工具与指令 |
| `handle_messages()` | 为 WebSocket 传输运行接收循环 |
| `update_session(config)` | 发送 provider 专用 session 更新 |
| `stream_audio(audio_chunk)` | 处理并上传一个 PCM 输入 chunk |
| `stream_image(image_b64, bypass_rate_limit=False)` | 发送或分析一个视觉帧 |
| `prime_context(text, skipped=False)` | 按 provider 语义注入启动/热切换上下文 |
| `create_response(instructions, skipped=False)` | 添加用户 item 并请求回复 |
| `inject_text_and_request_response(text, on_rejected=None)` | 原子执行主动文本注入与回复请求 |
| `prompt_ephemeral(...)` | 请求临时主动回复 |
| `cancel_response()` / `handle_interruption()` | 在 provider 支持范围内取消或截断当前回复 |
| `close()` | 停止后台任务、关闭 live 传输并释放媒体/工具状态 |

回调涵盖流式文本与音频、输入/输出转写、回复完成、工具调用、状态和连接错误。打断由生命周期方法处理；该客户端不存在通用的构造参数级 `on_interrupt` 事件契约。

## 音频与轮次检测

客户端接收不带采样率参数的 PCM bytes，并区分应用使用的两种采集格式：

- PC 的 480 sample / 960 byte chunk 以 48 kHz 输入，经过 RNNoise 路径并下采样为内部 16 kHz 流；
- 移动端的 512 sample / 1024 byte chunk 已是 16 kHz，会绕过 PC 降噪；
- OpenAI realtime 在最终发送步骤把内部流重采样为 24 kHz。

默认模式是 `TurnDetectionMode.SERVER_VAD`，但并非所有路由都提供 server VAD。Gemini、免费 Gemini proxy、livestream 和显式 manual 模式使用客户端轮次处理。本地 fallback 优先使用 RNNoise VAD，不可用时以带 sustain/grace 时序的 RMS 检测语音。

## 图片

Qwen、GLM、GPT、Gemini 和兼容的免费 Gemini 路由可以接收原生视觉帧。其他 realtime 模型会使用独立配置的视觉模型，把当前轮第一个相关帧转换成文本上下文。

原生帧受 `NATIVE_IMAGE_MIN_INTERVAL`（1.5 秒）限制；空闲采集会再乘以 `IMAGE_IDLE_RATE_MULTIPLIER`（5）。`bypass_rate_limit=True` 仅用于主动截图等刻意的一次性提示帧。

## 工具与主动注入

工具定义先被规范化，再编码成所选 provider 支持的 wire format；结果通过 provider 专用事件返回。带边界的滑动窗口 guard 会阻止 realtime 工具调用洪泛。

`inject_text_and_request_response()` 用于必须立即发声的主动回调。如果已有回复占用会话，它会拒绝或重排工作，而不是让两个响应流交错。

## 并发、背压与失败

音频与图片处理各有异步锁；发送受 semaphore 限制；fire-and-forget 工作会被跟踪，以便 `close()` 取消。HTTP/WebSocket 503 会触发短暂发送节流；致命帧、超时和传输错误会通知连接错误回调并关闭会话。

这些机制只保护一条 live provider 连接。Provider failover 以及文本/音频客户端之间的切换仍由会话管理器负责。
