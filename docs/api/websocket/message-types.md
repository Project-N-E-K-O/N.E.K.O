# WebSocket Message Types

Messages are JSON text frames, except for audio payloads. For audio, the server sends an `audio_chunk` JSON header frame followed by a separate raw binary frame carrying the PCM bytes (see the `audio_chunk` section below).

## Client → Server

### `start_session`

Initialize an LLM session.

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": true
}
```

### `stream_data`

Send user input (audio chunks or text).

**Audio input:**
```json
{
  "action": "stream_data",
  "input_type": "audio",
  "data": "<base64 encoded PCM audio>"
}
```

**Text input:**
```json
{
  "action": "stream_data",
  "input_type": "text",
  "data": "Hello, how are you?"
}
```

**Screen data:**
```json
{
  "action": "stream_data",
  "input_type": "screen",
  "data": "<base64 encoded screenshot>"
}
```

### `end_session`

Close the current session.

```json
{ "action": "end_session" }
```

### `pause_session`

Pause processing without closing the connection.

```json
{ "action": "pause_session" }
```

### `ping`

Keep-alive heartbeat.

```json
{ "action": "ping" }
```

## Server → Client

### `gemini_response`

Streamed text response from the LLM.

```json
{
  "type": "gemini_response",
  "text": "Hi there! How can I help you?",
  "isNewMessage": true,
  "turn_id": "<turn id>",
  "request_id": "<request id>"
}
```

### `audio_chunk`

Audio response (TTS output or direct LLM audio). The server first sends this JSON header frame, then sends the raw PCM audio bytes as a separate binary WebSocket frame (not embedded as base64). The `speech_id` identifies this turn's speech so the client can match it for barge-in.

```json
{
  "type": "audio_chunk",
  "speech_id": "<speech id for this turn>"
}
```

This is followed by a binary frame (raw PCM bytes); after receiving the header above, the client reads the next binary frame to decode and play the audio.

### `status`

Status messages about session state.

```json
{
  "type": "status",
  "message": "Session started successfully"
}
```

### `emotion`

Emotion label for driving model expressions.

```json
{
  "type": "emotion",
  "emotion": "happy"
}
```

### `catgirl_switched`

Notification that the active character changed server-side.

```json
{
  "type": "catgirl_switched",
  "new_catgirl": "new_character",
  "old_catgirl": "old_character"
}
```

The client should reconnect to `/ws/{new_catgirl}`.

### `reload_page`

Server requests the client to refresh the page.

```json
{
  "type": "reload_page",
  "message": "Configuration changed, please refresh"
}
```

### `agent_notification`

Agent task update notification.

```json
{
  "type": "agent_notification",
  "text": "Found relevant information about...",
  "source": "web_search",
  "status": "completed"
}
```

### `agent_task_update`

Detailed agent task status.

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

Agent system status snapshot.

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

Response to `ping`.

```json
{ "type": "pong" }
```
