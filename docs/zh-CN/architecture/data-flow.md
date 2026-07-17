# 数据流

## 浏览器与 Electron 入口

主服务器在 `/ws/{lanlan_name}` 暴露每个角色的 WebSocket。开发页面（`/`，由 `index.html` 提供）和 Electron 聊天窗口都使用相同的后端协议，以及 `frontend/react-neko-chat/` 中同一套 React 聊天实现。`/chat` 等 Electron 路由可能通过窗口 IPC 代理 socket，但消息契约不变。

已废弃的 `#chat-container` DOM 实现不在当前数据路径中。遗留的 `appendMessage()` 调用由 `static/app/app-chat-adapter.js` 拦截并路由到 React 聊天组件。

## WebSocket 生命周期

```text
客户端                         主服务器                       LLM 客户端
  │                                │                              │
  │── 连接 /ws/{name} ────────────>│ 接受并绑定 manager           │
  │── start_session(audio|text) ──>│── 创建 / 连接 ──────────────>│
  │<─ session_preparing / started ─│                              │
  │── stream_data ────────────────>│── 文本或 PCM 输入 ──────────>│
  │<─ gemini_response 增量 ────────│<─ 文本 / 输出回调 ───────────│
  │<─ audio_chunk 头 ──────────────│<─ 原生音频或外部 TTS ────────│
  │<─ 二进制 PCM 帧 ───────────────│                              │
  │<─ system: turn end ────────────│<─ 响应完成 ──────────────────│
  │── end_session ────────────────>│── 取消 / 关闭 / 清理 ───────>│
```

`start_session` 是异步操作。客户端必须等待 `session_started` 并处理 `session_failed`；`session_preparing` 表示静默启动窗口。音频模式创建 `OmniRealtimeClient`，文本模式创建 `OmniOfflineClient`。

启动或跨模式重建期间仍可能收到 `stream_data`。Manager 会缓存有序的文本/图片输入，并让音频通过有界队列串行处理，而不是假设每一帧都能立即发给上游。

## 客户端 → 服务器消息

所有控制消息都是 JSON 文本帧。常见聊天消息为：

```json
{ "action": "start_session", "input_type": "audio", "new_session": true }
{ "action": "start_session", "input_type": "text" }
{ "action": "stream_data", "input_type": "audio", "data": [0, 12, -8] }
{ "action": "stream_data", "input_type": "text", "data": "你好" }
{ "action": "stream_data", "input_type": "image", "data": "data:image/png;base64,..." }
{ "action": "end_session" }
{ "action": "ping" }
```

音频载荷是有符号 PCM 样本数组，不是 base64 字符串。浏览器采集路径通常发送 48 kHz 单声道块；实时会话路径会预处理并向上游发送 16 kHz 音频。

同一 socket 还承载 `greeting_check`、`avatar_interaction`、`screenshot_response`、`capture_bridge_*`、`goodbye_state`、`language_update`、`voice_play_start`、`voice_play_end` 和 telemetry 等操作。未知操作会返回结构化 `status` 错误。

每个角色只有一个连接世代是当前连接。新连接会替换已保存的 session ID；旧 socket 会被关闭，不能继续写入 manager。

## 服务器 → 客户端消息

重要 JSON 消息类型包括：

| 类型 | 用途 |
|---|---|
| `session_preparing`、`session_started`、`session_failed` | 带 `input_mode` 的启动状态 |
| `gemini_response` | 流式助手文本；历史名称在不同 provider 下继续沿用 |
| `user_transcript` | 识别出的用户语音 |
| `audio_chunk` | 包含 `speech_id` 的头；下一帧是二进制 PCM |
| `system` + `turn end` 或 `turn end agent_callback` | 轮次完成 |
| `status` | 结构化状态或错误载荷 |
| `expression`、`focus_state`、`focus_charge` | 角色表现状态 |
| `agent_status_update`、`agent_task_update`、`agent_notification` | Agent 状态和任务投递 |
| `request_screenshot`、`capture_bridge_request` | 客户端截图请求 |
| `session_ended_by_server`、`reload_page`、`catgirl_switched` | 生命周期恢复或角色变更 |
| `pong` | 心跳响应 |

外部 TTS 音频由 provider worker 规范为 48 kHz 单声道 PCM，先发送 `audio_chunk` JSON 头，再发送二进制帧；它不会作为 `audio_data` 嵌入 JSON。

## REST 请求流

```text
浏览器 / Electron renderer
        │
        ├─ /api/characters/* ──> 角色路由 ──────> ConfigManager
        ├─ /api/agent/* ───────> agent_router ─> Agent Server :48915
        └─ 其他 /api/* ────────> 领域路由 ──────> shared-state getter
```

路由通过 `main_routers/shared_state.py` 的 getter 获取长生命周期 manager。Agent 控制端点是主服务器代理；浏览器无需直接访问 48915 端口。

## Agent 事件流

```text
cross_server 的 turn/session end
        │ 近期消息 + 当前轮次附件
        ▼
MainServerAgentBridge ── ZMQ :48963 ──> AgentServerEventBridge
        ▲                                      │
        │ ACK / task_update / task_result      ▼
        └──────────── ZMQ :48962 ───── 通道分派
        │
        ├─ task_update ──> WebSocket ──> Task HUD
        └─ task_result ──> LLMSessionManager 回调队列 ──> 聊天投递
```

会话广播使用独立的 48961 PUB/SUB 地址。分析请求使用 48963 的可靠 PUSH/PULL 队列，并经 48962 的 Agent → 主通道确认。

## TTS 数据流

文本和音频不一定经过同一条合成路径：

- 文本会话总是向项目 TTS runtime 投递文本；若禁用 TTS，dummy worker 不生成音频。
- 当所选音色受支持时，语音会话使用 provider 原生实时音频。自定义音色和指定的外部 provider 改用项目 TTS runtime。
- 项目 TTS worker 通过线程队列接收文本，通过响应队列返回 48 kHz PCM。`send_speech()` 发送 `speech_id` 头和二进制数据。

这些流程的并发和中断规则见[会话管理](/architecture/session-management)与 [TTS 管线](/architecture/tts-pipeline)。
