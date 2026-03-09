# Realtime 客户端

**文件：** `main_logic/omni_realtime_client.py`

`OmniRealtimeClient` 管理与 Realtime API 提供商（Qwen、OpenAI、Gemini、Step、GLM）的 WebSocket 连接。

## 支持的提供商

| 提供商 | 协议 | 备注 |
|--------|------|------|
| Qwen (DashScope) | WebSocket | 主要提供商，Momo 语音，gummy-realtime-v1 转录 |
| OpenAI | WebSocket | GPT Realtime API（gpt-realtime-mini，语义 VAD，marin 语音） |
| Step | WebSocket | Step Audio（qingchunshaonv 语音，web_search 工具） |
| GLM | WebSocket | 智谱 Realtime（video_passive，tongtong 语音） |
| Gemini | Google GenAI SDK | 使用 SDK 封装（`_connect_gemini`），非原始 WebSocket |
| Free | WebSocket | 同 Step 配置但不带工具 |

## 关键方法

### `connect()`

与提供商的 Realtime API 端点建立 WebSocket 连接。Gemini 使用专用的 `_connect_gemini()` 路径，通过 Google GenAI SDK 连接。

### `send_text(text)`

将用户文本输入发送到 LLM。

### `send_audio(audio_bytes, sample_rate)`

将用户音频块流式传输到 LLM。音频以原始 PCM 数据格式发送。

### `send_screenshot(base64_data)`

发送截图用于多模态理解。受 `NATIVE_IMAGE_MIN_INTERVAL`（默认 1.5 秒）的速率限制。

### `stream_proactive(instruction)`

使用给定指令发起主动（角色发起的）消息流。

## 事件处理器

| 事件 | 用途 |
|------|------|
| `on_text_delta()` | LLM 的流式文本响应 |
| `on_audio_delta()` | 流式音频响应 |
| `on_input_transcript()` | 用户语音转文本（STT） |
| `on_output_transcript()` | LLM 的文本输出 |
| `on_interrupt()` | 用户打断了 LLM 的输出 |
| `on_response_done()` | 完整响应完成 |
| `on_repetition_detected()` | 检测到输出重复 |
| `on_response_discarded()` | 响应被丢弃（重复、错误） |

## 轮次检测

客户端默认使用**服务端 VAD**（语音活动检测）。由 LLM 提供商决定用户何时结束发言，从而实现自然的对话轮转。

## 图像节流

屏幕截图受速率限制，以避免对 API 造成过大负载：

- **正在说话时**：每 `NATIVE_IMAGE_MIN_INTERVAL` 秒（1.5 秒）发送一次图像
- **空闲（无语音）**：间隔乘以 `IMAGE_IDLE_RATE_MULTIPLIER`（5 倍 = 7.5 秒）

## 视觉分析

`_analyze_image_with_vision_model(image_b64)` 方法提供独立的视觉模型分析，用于截图分析，与主 LLM 对话并行使用。
