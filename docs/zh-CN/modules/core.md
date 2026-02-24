# LLMSessionManager

**文件：** `main_logic/core.py`（约 2300 行）

`LLMSessionManager` 是 N.E.K.O. 的核心——每个角色一个实例，管理整个对话生命周期。

## 职责

- WebSocket 连接管理
- LLM 会话创建和热切换
- TTS 管道协调
- 音频重采样（24kHz → 48kHz）
- Agent 回调注入
- 翻译支持

## 关键方法

### `start_session(websocket, new, input_mode)`

初始化一个新的 LLM 会话：

1. 使用角色配置创建 `OmniRealtimeClient`
2. 通过 WebSocket 连接到 Realtime API
3. 启动 TTS 工作线程（如果启用了语音输出）
4. 在后台开始准备下一个会话以进行热切换

### `stream_data(message)`

处理传入的用户输入：

- **音频**：将 PCM 音频块发送到 Realtime API 客户端
- **文本**：将文本消息发送到 LLM
- **屏幕**：发送截图进行多模态理解

### `handle_new_message()`

当 LLM 产生输出时调用：

- 将文本输出路由到 TTS 队列（或直接发送到 WebSocket）
- 发送情感标签用于表情映射
- 处理 Agent 通知

### `end_session(by_server)`

关闭当前会话并触发热切换：

1. 关闭 Realtime API WebSocket
2. 调用 `_perform_final_swap_sequence()` 实现无缝过渡
3. 刷新切换期间缓存的音频

### `cleanup(expected_websocket)`

当 WebSocket 断开连接时释放所有资源。

### `trigger_agent_callbacks()`

将待处理的 Agent 结果注入到下一轮 LLM 对话中，使角色能够引用 Agent 的发现。

### `translate_if_needed(text)`

当用户语言与角色语言不同时翻译文本。

## 线程模型

```
主异步循环 (FastAPI)
  ├── WebSocket 接收循环
  ├── LLM 事件处理器 (on_text_delta, on_audio_delta)
  │
  ├── TTS 工作线程（队列消费者）
  │
  └── 后台会话准备（热切换）
```

## 集成点

- **WebSocket Router** → 调用 `start_session`、`stream_data`、`end_session`
- **Agent Event Bridge** → 通过 `pending_agent_callbacks` 传递结果
- **Config Manager** → 提供角色数据和 API 配置
- **TTS Client** → `get_tts_worker()` 工厂函数创建 TTS 工作器
