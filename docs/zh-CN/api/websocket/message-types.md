# WebSocket 消息类型

客户端文本帧使用 `action`，服务端文本帧使用 `type`。下列清单由主 handler 和第一方前端反向枚举得到。标为内部的字段属于第一方 UI 流程，不随公共协议版本承诺稳定。

## 客户端 → 服务端

### 对话 action

#### `start_session`

```json
{ "action": "start_session", "input_type": "audio", "new_session": false }
```

有效 `input_type`：`audio`、`screen`、`camera`、`text`、`avatar_drop_image`、`user_image`。

#### `stream_data`

文本：

```json
{
  "action": "stream_data",
  "input_type": "text",
  "data": "你好",
  "request_id": "client-turn-id",
  "memory_text": "可选：替代脚手架写入记忆的文本",
  "source": "optional-source"
}
```

图片（`screen`、`camera`、`avatar_drop_image` 或 `user_image`）：

```json
{
  "action": "stream_data",
  "input_type": "user_image",
  "data": "data:image/jpeg;base64,...",
  "request_id": "client-turn-id",
  "avatar_position": { "x": 10, "y": 20, "width": 300, "height": 500 }
}
```

音频使用带符号 16 位 PCM **样本数字数组**，不是 base64，也不是客户端二进制帧：

```json
{ "action": "stream_data", "input_type": "audio", "data": [0, -12, 48, 103] }
```

`avatar_position` 是与新鲜屏幕/图片配对的可选元数据；省略时会清除之前缓存的位置。

#### `end_session` 与 `pause_session`

```json
{ "action": "end_session", "reason": "user_stop", "goodbye_active": false }
```

```json
{ "action": "pause_session" }
```

两者都会结束当前 provider session；`pause_session` 还将 manager 标为空闲。应用 WebSocket 保持连接。

#### `avatar_interaction`

短生命周期的模型手势/触摸请求。第一方 payload 包含 `interaction_id`、`tool_id`、`action_id`、`target: "avatar"`、`timestamp`、`intensity`，按需要含 `touch_zone`/`pointer`。结果由 `avatar_interaction_ack` 返回。

### UI 与生命周期 action

| Action | 关键字段 | 行为 |
|---|---|---|
| `ping` | — | 返回 `pong`。 |
| `language_update` | `language` | 通用语言更新后，分发阶段为空操作。 |
| `greeting_check` | `is_switch`、`reason`、`language` | 仅角色切换或重连间隔超过 15 秒时触发问候，并同步第一方 focus/agent 状态。 |
| `cat_greeting_check` | `cat_duration_seconds`、`tier`、`was_auto` | 请求从猫形态返回的问候；时长限制到 0–7 天。 |
| `goodbye_state` | `active`、`reason` | 启用/清除静默告别投递 gate。 |
| `voice_play_start` | `turnId`/`turn_id`、`source` | 报告前端缓冲音频真正开始播放。 |
| `voice_play_end` | `turnId`/`turn_id`、`source` | 报告前端音频队列真正清空。 |

播放边界对主动消息仲裁很重要：上游生成结束早于实际播放结束。

### 截图桥接 action（内部）

| Action | 用途 |
|---|---|
| `capture_bridge_status` | 注册/更新前端截图客户端及能力。 |
| `capture_bridge_response` | 用关联字段完成截图桥请求。 |
| `screenshot_response` | 完成旧版 `request_screenshot`；`data` 为 data URL/base64 图片，`avatar_position` 可选。 |

### `telemetry`（内部、尽力而为）

```json
{ "action": "telemetry", "kind": "counter", "name": "chat_sent", "value": 1, "dims": { "surface": "index_wide" } }
```

`kind` 为 `counter`、`histogram` 或 `event`（event 使用 `fields`）。后端限制名称、键、值和字段数量，丢弃不支持类型和非有限数值，且不返回 ack。禁止在 telemetry 中放用户原文或角色名。

任意 action 还可携带 `language`。

## 服务端 → 客户端

### 会话生命周期

| Type | 字段 | 含义 |
|---|---|---|
| `session_preparing` | `input_mode` | Provider 启动中。 |
| `session_started` | `input_mode` | 请求的 `audio` 或 `text` 模式已就绪。 |
| `session_failed` | `input_mode` | 启动失败；细节通常在 `status`。 |
| `session_ended_by_server` | `input_mode` | 后端/上游结束 provider session。 |
| `catgirl_switched` | `new_catgirl`、`old_catgirl` | 应重新连接新角色路由。 |
| `pong` | — | `ping` 的响应。 |

### 文本、音频与恢复

#### `gemini_response`

名称是历史遗留，现在用于多个 provider 的流式助手文本：

```json
{
  "type": "gemini_response",
  "text": "你好",
  "isNewMessage": true,
  "turn_id": "server-turn-id",
  "request_id": "client-turn-id",
  "metadata": { "source": "optional" }
}
```

首个可见 chunk 的 `isNewMessage` 为 true，后续 chunk 追加到相同 `turn_id`。主动消息或服务端发起轮次的 `request_id` 可为 null。

#### `audio_chunk`

```json
{ "type": "audio_chunk", "speech_id": "speech-id" }
```

头之后严格跟一个二进制音频帧。用 `speech_id` 关联；参见[音频流](./audio-streaming)。

#### 恢复与转写事件

| Type | 关键字段 | 用途 |
|---|---|---|
| `response_discarded` | `reason`、`attempt`、`max_attempts`、`will_retry`、`message`、`request_id` | 回滚/清除被拒绝的部分响应或准备重试；`message` 自身也可能是结构化 JSON。 |
| `user_transcript` | 转写/轮次元数据 | 第一方实时转写显示。 |
| `user_activity` | 轮次/打断元数据 | 插话与用户活动协调。 |
| `auto_close_mic` | `reason_code`、`api_type`、`message` | 静音超时关闭语音会话。 |
| `repetition_warning` | `name` | 重复恢复已重置对话状态。 |

### 状态与显示

| Type | 关键字段 | 用途 |
|---|---|---|
| `status` | `message` | `message` 是含 `{ code, details? }` 的 **JSON 字符串**，需再次解析。 |
| `expression` | 表情 payload | 驱动 Live2D/VRM/MMD/PNGTuber 表情。 |
| `focus_state` | `active` | 进入/退出专注认知显示。 |
| `focus_charge` | `charge` 及时间/模式字段 | 更新边缘光电量。 |
| `focus_thinking` | `active` | 切换临时思考指示。 |
| `topic_hint` | `author`、`turn_id` | 仅前端显示的预告气泡，不进入聊天记忆。 |
| `cancel_topic_hint` | `turn_id` | 删除孤立预告。 |
| `reload_page` | `message` | 配置变化；`message` 也是状态式 JSON 字符串。 |

### 第一方工作流事件

以下是当前 UI 集成事件，不属于稳定外部契约：

- Agent：`agent_notification`、`agent_task_update`、`agent_status_update`。
- 截图：`request_screenshot`、`capture_bridge_request`、`screen_share_error`。
- 小游戏：`mini_game_invite_options`、`mini_game_invite_resolved`、`game_window_state_change`。
- 音乐/工具：`music_play_url`、`music_allowlist_add`。
- 活动/引导：`activity_context_prompt`。
- 兼容/同步：`system`、`cozy_audio`。

`avatar_interaction_ack` 也是第一方事件，但信封较小且明确：

```json
{
  "type": "avatar_interaction_ack",
  "interaction_id": "id",
  "accepted": true,
  "reason": "accepted",
  "turn_id": "turn-id"
}
```

客户端必须安全忽略未知服务端 `type`，以免新增 UI 事件破坏连接。
