# WebSocket 消息类型

所有消息均为 JSON 文本帧。

## 客户端 → 服务器

### `start_session`

初始化 LLM 会话。

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": true
}
```

### `stream_data`

发送用户输入（音频块或文本）。

**音频输入：**
```json
{
  "action": "stream_data",
  "input_type": "audio",
  "data": "<base64 encoded PCM audio>"
}
```

**文本输入：**
```json
{
  "action": "stream_data",
  "input_type": "text",
  "data": "Hello, how are you?"
}
```

**屏幕数据：**
```json
{
  "action": "stream_data",
  "input_type": "screen",
  "data": "<base64 encoded screenshot>"
}
```

### `end_session`

关闭当前会话。

```json
{ "action": "end_session" }
```

### `pause_session`

暂停处理但不关闭连接。

```json
{ "action": "pause_session" }
```

### `ping`

保活心跳。

```json
{ "action": "ping" }
```

## 服务器 → 客户端

### `text`

来自 LLM 的流式文本响应。

```json
{
  "type": "text",
  "text": "Hi there! How can I help you?"
}
```

### `audio`

音频响应（TTS 输出或 LLM 直接音频）。

```json
{
  "type": "audio",
  "audio_data": "<base64 encoded PCM 48kHz>"
}
```

### `status`

关于会话状态的状态消息。

```json
{
  "type": "status",
  "message": "Session started successfully"
}
```

### `emotion`

用于驱动模型表情的情感标签。

```json
{
  "type": "emotion",
  "emotion": "happy"
}
```

### `catgirl_switched`

服务端切换了活动角色的通知。

```json
{
  "type": "catgirl_switched",
  "new_catgirl": "new_character",
  "old_catgirl": "old_character"
}
```

客户端应重新连接到 `/ws/{new_catgirl}`。

### `reload_page`

服务器请求客户端刷新页面。

```json
{
  "type": "reload_page",
  "message": "Configuration changed, please refresh"
}
```

### `agent_notification`

智能体任务更新通知。

```json
{
  "type": "agent_notification",
  "text": "Found relevant information about...",
  "source": "web_search",
  "status": "completed"
}
```

### `agent_task_update`

智能体任务详细状态。

```json
{
  "type": "agent_task_update",
  "task": {
    "id": "task-uuid",
    "status": "running",
    "progress": 50
  }
}
```

### `agent_status_update`

智能体系统状态快照。

```json
{
  "type": "agent_status_update",
  "snapshot": {
    "active_tasks": 1,
    "flags": { "agent_enabled": true }
  }
}
```

### `pong`

对 `ping` 的响应。

```json
{ "type": "pong" }
```
