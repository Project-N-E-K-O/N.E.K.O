# LLMSessionManager

**文件：** `main_logic/core.py`（约 2460 行）

`LLMSessionManager` 是 N.E.K.O. 的核心——每个角色一个实例，管理整个对话生命周期。

## 职责

- WebSocket 连接管理
- LLM 会话创建和热切换
- TTS 管道协调
- 音频重采样（24kHz → 48kHz，通过 soxr）
- Agent 回调注入
- 主动消息推送
- 截图请求/响应
- 翻译支持

## 关键方法

### 会话生命周期

### `start_session(websocket, new, input_mode)`

初始化一个新的 LLM 会话：

1. 使用角色配置创建 `OmniRealtimeClient`（或 `OmniOfflineClient`）
2. 通过 WebSocket 连接到 Realtime API
3. 启动 TTS 工作线程（如果启用了语音输出）
4. 在后台开始准备下一个会话以进行热切换

### `stream_data(message)`

处理传入的用户输入：

- **音频**：将 PCM 音频块发送到 Realtime API 客户端
- **文本**：将文本消息发送到 LLM
- **屏幕/摄像头**：发送截图进行多模态理解

### `end_session(by_server)`

关闭当前会话并触发热切换：

1. 关闭 Realtime API WebSocket
2. 调用 `_perform_final_swap_sequence()` 实现无缝过渡
3. 刷新切换期间缓存的音频

### `cleanup(expected_websocket)`

当 WebSocket 断开连接时释放所有资源。

### 回调处理器

| 处理器 | 用途 |
|--------|------|
| `handle_new_message()` | 将 LLM 输出路由到 TTS 或 WebSocket |
| `handle_text_data(text, is_first_chunk)` | 处理流式文本块 |
| `handle_audio_data(audio_data)` | 处理流式音频输出 |
| `handle_response_complete()` | 完整 LLM 响应完成时调用 |
| `handle_response_discarded(reason, ...)` | 响应被丢弃时调用（重复等） |
| `handle_input_transcript(transcript)` | 用户语音转文本结果 |
| `handle_output_transcript(text, is_first_chunk)` | LLM 输出的文本（与音频并行） |
| `handle_silence_timeout()` | 用户静默超时时触发 |
| `handle_connection_error(message)` | LLM 连接失败处理 |
| `handle_repetition_detected()` | 检测到重复时触发 |

### Agent 集成

| 方法 | 用途 |
|------|------|
| `trigger_agent_callbacks()` | 将待处理的 Agent 结果传递到 LLM 对话轮 |
| `enqueue_agent_callback(callback)` | 将 Agent 结果加入注入队列 |
| `drain_agent_callbacks_for_llm()` | 收集所有排队回调作为 LLM 上下文 |
| `update_agent_flags(flags)` | 更新 Agent 能力标志 |

### 主动消息

| 方法 | 用途 |
|------|------|
| `deliver_text_proactively(...)` | 推送角色主动发起的消息 |
| `prepare_proactive_delivery(min_idle_secs)` | 检查空闲时间并准备推送 |
| `feed_tts_chunk(text)` | 在主动推送期间向 TTS 输入文本块 |
| `finish_proactive_delivery(full_text)` | 完成主动消息的完整文本 |

### 截图机制

| 方法 | 用途 |
|------|------|
| `request_fresh_screenshot(timeout)` | 向前端请求截图 |
| `resolve_screenshot_request(b64)` | 处理前端的截图响应 |

### 热切换内部方法

| 方法 | 用途 |
|------|------|
| `_perform_final_swap_sequence()` | 执行热切换过渡 |
| `_background_prepare_pending_session()` | 后台预热下一个会话 |
| `_flush_hot_swap_audio_cache()` | 刷新切换期间缓存的音频 |

### `translate_if_needed(text)`

当用户语言与角色语言不同时翻译文本。

## 线程模型

```
主异步循环 (FastAPI)
  ├── WebSocket 接收循环
  ├── LLM 事件处理器 (on_text_delta, on_audio_delta, on_interrupt, ...)
  │
  ├── TTS 工作线程（队列消费者）
  │
  └── 后台会话准备（热切换）
```

## 集成点

- **WebSocket Router** → 调用 `start_session`、`stream_data`、`end_session`
- **Agent Event Bus** → 通过 `enqueue_agent_callback` 传递结果
- **Config Manager** → 提供角色数据和 API 配置
- **TTS Client** → `get_tts_worker()` 工厂函数创建 TTS 工作器
- **Cross Server** → 向监控和记忆服务器转发消息
