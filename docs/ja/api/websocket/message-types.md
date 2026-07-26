# WebSocket Message Types

Client text frame は `action`、server text frame は `type` を使います。以下は main handler と同梱 frontend から reverse-enumerate したものです。internal とした field は first-party UI flow で、public protocol version の安定性を保証しません。

## Client → server

### Conversation action

#### `start_session`

```json
{ "action": "start_session", "input_type": "audio", "new_session": false }
```

有効 `input_type`: `audio`、`screen`、`camera`、`text`、`avatar_drop_image`、`user_image`。

#### `stream_data`

Text:

```json
{
  "action": "stream_data",
  "input_type": "text",
  "data": "こんにちは",
  "request_id": "client-turn-id",
  "memory_text": "scaffold の代わりに記録する任意 text",
  "source": "optional-source"
}
```

Image (`screen`、`camera`、`avatar_drop_image`、`user_image`):

```json
{
  "action": "stream_data",
  "input_type": "user_image",
  "data": "data:image/jpeg;base64,...",
  "request_id": "client-turn-id",
  "avatar_position": { "x": 10, "y": 20, "width": 300, "height": 500 }
}
```

Audio は signed 16-bit PCM **sample 数値配列**で、base64 でも client binary frame でもありません。

```json
{ "action": "stream_data", "input_type": "audio", "data": [0, -12, 48, 103] }
```

`avatar_position` は fresh screen/image と対になる任意 metadata。省略すると以前の cached position を clear します。

#### `end_session` / `pause_session`

```json
{ "action": "end_session", "reason": "user_stop", "goodbye_active": false }
```

```json
{ "action": "pause_session" }
```

いずれも現在 provider session を終了し、`pause_session` は manager を idle にします。Application WebSocket は接続を維持します。

#### `avatar_interaction`

Ephemeral avatar gesture/touch request。同梱 payload は `interaction_id`、`tool_id`、`action_id`、`target: "avatar"`、`timestamp`、`intensity`、必要に応じ `touch_zone`/`pointer`。結果は `avatar_interaction_ack`。

### UI / lifecycle action

| Action | 主な field | 挙動 |
|---|---|---|
| `ping` | — | `pong` を返します。 |
| `language_update` | `language` | universal language update 後は no-op dispatch。 |
| `greeting_check` | `is_switch`、`reason`、`language` | character switch または reconnect gap >15 秒で greeting。first-party focus/agent state も同期。 |
| `cat_greeting_check` | `cat_duration_seconds`、`tier`、`was_auto` | cat form から戻る greeting。duration は 0–7 日へ clamp。 |
| `goodbye_state` | `active`、`reason` | silent-goodbye delivery gate を設定/解除。 |
| `voice_play_start` | `turnId`/`turn_id`、`source` | frontend buffered audio が実際に再生開始。 |
| `voice_play_end` | `turnId`/`turn_id`、`source` | frontend audio queue が完全に drain。 |

Playback boundary は proactive arbitration に重要です。Upstream generation end は audible playback end より早いためです。

### Capture bridge action（internal）

| Action | 用途 |
|---|---|
| `capture_bridge_status` | frontend capture client/capability を登録・更新。 |
| `capture_bridge_response` | correlation field で request を resolve。 |
| `screenshot_response` | legacy `request_screenshot` を resolve。`data` は data URL/base64 image、任意 `avatar_position`。 |

### `telemetry`（internal、best effort）

```json
{ "action": "telemetry", "kind": "counter", "name": "chat_sent", "value": 1, "dims": { "surface": "index_wide" } }
```

`kind` は `counter`、`histogram`、`event`（event は `fields`）。backend は name/key/value/count を cap し、unsupported type/non-finite value を drop、ack は返しません。User text や character name を telemetry に入れないでください。

任意 action に `language` を付けられます。

## Server → client

### Session lifecycle

| Type | Field | 意味 |
|---|---|---|
| `session_preparing` | `input_mode` | Provider startup 中。 |
| `session_started` | `input_mode` | `audio` / `text` mode ready。 |
| `session_failed` | `input_mode` | Startup failure。通常 detail は `status`。 |
| `session_ended_by_server` | `input_mode` | Backend/upstream が provider session を終了。 |
| `catgirl_switched` | `new_catgirl`、`old_catgirl` | 新 character route へ reconnect。 |
| `pong` | — | `ping` response。 |

### Text、audio、recovery

#### `gemini_response`

名称は歴史的で、現在は複数 provider の streamed assistant text に使います。

```json
{
  "type": "gemini_response",
  "text": "こんにちは",
  "isNewMessage": true,
  "turn_id": "server-turn-id",
  "request_id": "client-turn-id",
  "metadata": { "source": "optional" }
}
```

最初の visible chunk は `isNewMessage: true`、後続は同じ `turn_id` へ append。Proactive/server-origin turn は `request_id: null` の場合があります。

#### `audio_chunk`

```json
{ "type": "audio_chunk", "speech_id": "speech-id" }
```

直後に binary audio frame が 1 つ続きます。`speech_id` で対応付けます。[Audio Streaming](./audio-streaming) を参照。

#### Recovery / transcript event

| Type | 主な field | 用途 |
|---|---|---|
| `response_discarded` | `reason`、`attempt`、`max_attempts`、`will_retry`、`message`、`request_id` | rejected partial response を rollback/clear、または retry 準備。`message` 自体が structured JSON の場合あり。 |
| `user_transcript` | transcript/turn metadata | First-party live transcript 表示。 |
| `user_activity` | turn/interruption metadata | Barge-in / user activity coordination。 |
| `auto_close_mic` | `reason_code`、`api_type`、`message` | Silence timeout で voice session close。 |
| `repetition_warning` | `name` | Repetition recovery で conversation state reset。 |

### Status / display

| Type | 主な field | 用途 |
|---|---|---|
| `status` | `message` | `message` は `{ code, details? }` を含む **JSON encoded string**。再 parse 必須。 |
| `expression` | expression payload | Live2D/VRM/MMD/PNGTuber expression 駆動。 |
| `focus_state` | `active` | Focus cognition display enter/leave。 |
| `focus_charge` | `charge` と timing/mode | Edge glow charge 更新。 |
| `focus_thinking` | `active` | Transient thinking indicator。 |
| `topic_hint` | `author`、`turn_id` | Frontend-only teaser。chat memory には入りません。 |
| `cancel_topic_hint` | `turn_id` | orphan teaser を削除。 |
| `reload_page` | `message` | Config change。`message` は status-style JSON string。 |

### First-party workflow event

現在の UI integration event で、stable external contract ではありません。

- Agent: `agent_notification`、`agent_task_update`、`agent_status_update`。
- Capture: `request_screenshot`、`capture_bridge_request`、`screen_share_error`。
- Mini-game: `mini_game_invite_options`、`mini_game_invite_resolved`、`game_window_state_change`。
- Music/tool: `music_play_url`、`music_allowlist_add`。
- Activity/onboarding: `activity_context_prompt`。
- Legacy/sync: `system`、`cozy_audio`。

`avatar_interaction_ack` も first-party ですが envelope は小さく明示的です。

```json
{
  "type": "avatar_interaction_ack",
  "interaction_id": "id",
  "accepted": true,
  "reason": "accepted",
  "turn_id": "turn-id"
}
```

Additive UI event で壊れないよう、unknown server `type` は安全に無視してください。
