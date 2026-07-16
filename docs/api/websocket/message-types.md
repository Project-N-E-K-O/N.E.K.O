# WebSocket Message Types

Client text frames use `action`; server text frames use `type`. The lists below are reverse-enumerated from the main handler and the bundled frontend. Fields marked internal belong to first-party UI flows and can change without a public protocol version bump.

## Client → server actions

### Conversation actions

#### `start_session`

```json
{ "action": "start_session", "input_type": "audio", "new_session": false }
```

Valid `input_type`: `audio`, `screen`, `camera`, `text`, `avatar_drop_image`, `user_image`.

#### `stream_data`

Text:

```json
{
  "action": "stream_data",
  "input_type": "text",
  "data": "Hello",
  "request_id": "client-turn-id",
  "memory_text": "optional text recorded instead of a scaffold",
  "source": "optional-source"
}
```

Image (`screen`, `camera`, `avatar_drop_image`, or `user_image`):

```json
{
  "action": "stream_data",
  "input_type": "user_image",
  "data": "data:image/jpeg;base64,...",
  "request_id": "client-turn-id",
  "avatar_position": { "x": 10, "y": 20, "width": 300, "height": 500 }
}
```

Audio uses a JSON array of signed 16-bit PCM sample values, **not base64** and not a client binary frame:

```json
{
  "action": "stream_data",
  "input_type": "audio",
  "data": [0, -12, 48, 103]
}
```

`avatar_position` is optional metadata paired with a fresh screen/image frame. Omitting it clears the previously cached position.

#### `end_session` and `pause_session`

```json
{ "action": "end_session", "reason": "user_stop", "goodbye_active": false }
```

```json
{ "action": "pause_session" }
```

Both end the current provider session; `pause_session` additionally marks the manager idle. The application WebSocket remains connected.

#### `avatar_interaction`

Ephemeral avatar gesture/touch request. The first-party payload includes `interaction_id`, `tool_id`, `action_id`, `target: "avatar"`, `timestamp`, `intensity`, and when applicable `touch_zone`/`pointer`. Completion is reported by `avatar_interaction_ack`.

### UI and lifecycle actions

| Action | Key fields | Behavior |
|---|---|---|
| `ping` | — | Returns `pong`. |
| `language_update` | `language` | No-op dispatch after the universal language update. |
| `greeting_check` | `is_switch`, `reason`, `language` | Triggers greeting only for a character switch or a reconnect gap over 15 seconds; also resynchronizes first-party focus/agent state. |
| `cat_greeting_check` | `cat_duration_seconds`, `tier`, `was_auto` | Requests the return-from-cat-form greeting; duration is clamped to 0–7 days. |
| `goodbye_state` | `active`, `reason` | Enables/clears the silent-goodbye delivery gate. |
| `voice_play_start` | `turnId`/`turn_id`, `source` | Reports that buffered frontend audio actually began playing. |
| `voice_play_end` | `turnId`/`turn_id`, `source` | Reports that the frontend audio queue fully drained. |

Playback boundary events are important for proactive-chat arbitration: upstream generation completion is earlier than audible playback completion.

### Capture bridge actions (internal)

| Action | Purpose |
|---|---|
| `capture_bridge_status` | Register/update the connected frontend capture client and its capabilities. |
| `capture_bridge_response` | Resolve a capture-bridge request by its correlation fields. |
| `screenshot_response` | Resolve the legacy `request_screenshot` flow. `data` is a data URL/base64 image; `avatar_position` is optional. |

### `telemetry` (internal, best effort)

```json
{ "action": "telemetry", "kind": "counter", "name": "chat_sent", "value": 1, "dims": { "surface": "index_wide" } }
```

`kind` is `counter`, `histogram`, or `event` (`fields` replaces `dims` for event). The backend caps names, keys, values, and field count, drops unsupported types/non-finite values, and does not acknowledge delivery. Do not put user text or character names in telemetry fields.

Any action may also include `language`.

## Server → client events

### Session lifecycle

| Type | Fields | Meaning |
|---|---|---|
| `session_preparing` | `input_mode` | Provider startup is in progress. |
| `session_started` | `input_mode` | Requested `audio` or `text` mode is ready. |
| `session_failed` | `input_mode` | Startup failed; a `status` event normally carries detail. |
| `session_ended_by_server` | `input_mode` | Backend/upstream ended the provider session. |
| `catgirl_switched` | `new_catgirl`, `old_catgirl` | Reconnect to the new character route. |
| `pong` | — | Reply to `ping`. |

### Text, audio, and recovery

#### `gemini_response`

The name is historical and is used for streamed assistant text from multiple providers:

```json
{
  "type": "gemini_response",
  "text": "Hello",
  "isNewMessage": true,
  "turn_id": "server-turn-id",
  "request_id": "client-turn-id",
  "metadata": { "source": "optional" }
}
```

`isNewMessage` is true on the first visible chunk; subsequent chunks append to the same `turn_id`. `request_id` may be null for proactive or server-originated turns.

#### `audio_chunk`

```json
{ "type": "audio_chunk", "speech_id": "speech-id" }
```

Exactly one binary audio frame follows the header. Correlate it with `speech_id`; see [Audio Streaming](./audio-streaming).

#### Recovery and transcript events

| Type | Important fields | Purpose |
|---|---|---|
| `response_discarded` | `reason`, `attempt`, `max_attempts`, `will_retry`, `message`, `request_id` | Roll back/clear a rejected partial response or prepare a retry. `message` can itself contain structured JSON. |
| `user_transcript` | transcript/turn metadata | First-party live transcription display. |
| `user_activity` | turn/interruption metadata | Barge-in and user-activity coordination. |
| `auto_close_mic` | `reason_code`, `api_type`, `message` | Silence timeout closed the voice session. |
| `repetition_warning` | `name` | Repetition recovery reset conversation state. |

### Status and display state

| Type | Important fields | Purpose |
|---|---|---|
| `status` | `message` | `message` is a **JSON-encoded string** containing `{ code, details? }`; parse it again. |
| `expression` | expression payload | Drive Live2D/VRM/MMD/PNGTuber expression state. |
| `focus_state` | `active` | Enter/leave focused cognition display. |
| `focus_charge` | `charge`, timing/mode fields | Update focus edge-glow charge. |
| `focus_thinking` | `active` | Toggle the transient thinking indicator. |
| `topic_hint` | `author`, `turn_id` | Frontend-only prelude bubble, not chat memory. |
| `cancel_topic_hint` | `turn_id` | Remove an orphaned prelude. |
| `reload_page` | `message` | Configuration changed; `message` is another status-style encoded JSON string. |

### First-party workflow events

These are current UI integration events, not a stable external contract:

- Agent: `agent_notification`, `agent_task_update`, `agent_status_update`.
- Capture: `request_screenshot`, `capture_bridge_request`, `screen_share_error`.
- Mini-games: `mini_game_invite_options`, `mini_game_invite_resolved`, `game_window_state_change`.
- Music/tools: `music_play_url`, `music_allowlist_add`.
- Activity/onboarding: `activity_context_prompt`.
- Legacy/synchronization: `system`, `cozy_audio`.

`avatar_interaction_ack` is also first-party but has a small explicit envelope:

```json
{
  "type": "avatar_interaction_ack",
  "interaction_id": "id",
  "accepted": true,
  "reason": "accepted",
  "turn_id": "turn-id"
}
```

Unknown server `type` values must be ignored safely so additive UI events do not break clients.
