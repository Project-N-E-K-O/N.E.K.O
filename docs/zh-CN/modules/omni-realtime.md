# Realtime 客户端

**文件：** `main_logic/omni_realtime_client.py`

`OmniRealtimeClient` 管理与 Realtime API 提供商（Qwen、OpenAI、Gemini、Step、GLM）的 WebSocket 连接。

## 支持的提供商

| 提供商 | 协议 | 备注 |
|--------|------|------|
| Qwen (DashScope) | WebSocket | 主要提供商，测试最充分 |
| OpenAI | WebSocket | GPT Realtime API |
| Step | WebSocket | Step Audio |
| GLM | WebSocket | 智谱 Realtime |
| Gemini | Google GenAI SDK | 使用 SDK 封装，非原始 WebSocket |

## 关键方法

### `connect()`

与提供商的 Realtime API 端点建立 WebSocket 连接。

### `prime_context(text, skipped=False)`

将用户文本作为上下文注入对话。当 `skipped=True`（或在 Qwen 上）时，文本会追加到会话指令中而不触发模型响应；当 `skipped=False`（GPT/GLM/Step）时，会注入一条一次性用户消息并触发响应。

### `create_response(instructions, skipped=False)`

创建一条 user 角色的对话消息并触发 LLM 响应。用于会话中途需要模型立即回复的场景。

### `inject_text_and_request_response(text, *, on_rejected=None)`

在一次调用中注入一条 user 角色的文本条目并显式触发响应。由语音模式的主动搭话路径（agent 任务回调 / 插件 `push_message` `ai_behavior="respond"`）使用，让模型立即说出结果，而无需等待下一个用户轮次。

### `stream_audio(audio_chunk)`

将一个原始 PCM 音频块流式传输到 LLM。输入采样率会根据音频块大小自动检测（480 个采样点 = 来自 PC 的 48 kHz，会经过 RNNoise 降噪并下采样到 16 kHz；512 个采样点 = 来自移动端的 16 kHz，直接透传），因此无需传入采样率参数。

### `stream_image(image_b64, *, bypass_rate_limit=False)`

将截图 / 相机画面流式传输用于多模态理解。受 `NATIVE_IMAGE_MIN_INTERVAL`（默认 1.5 秒）的速率限制；传入 `bypass_rate_limit=True` 可为单张刻意发送的提示图（例如主动回调的截图）跳过节流。

## 事件处理器

| 事件 | 用途 |
|------|------|
| `on_text_delta()` | LLM 的流式文本响应 |
| `on_audio_delta()` | 流式音频响应 |
| `on_input_transcript()` | 用户语音转文本（STT） |
| `on_output_transcript()` | LLM 的文本输出 |
| `on_interrupt()` | 用户打断了 LLM 的输出 |

## 轮次检测

客户端默认使用**服务端 VAD**（语音活动检测）。由 LLM 提供商决定用户何时结束发言，从而实现自然的对话轮转。

## 图像节流

屏幕截图受速率限制，以避免对 API 造成过大负载：

- **正在说话时**：每 `NATIVE_IMAGE_MIN_INTERVAL` 秒（1.5 秒）发送一次图像
- **空闲（无语音）**：间隔乘以 `IMAGE_IDLE_RATE_MULTIPLIER`（5 倍 = 7.5 秒）
