# WebSocket Message Types

All messages are JSON text frames.

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

### `text`

Streamed text response from the LLM.

```json
{
  "type": "text",
  "text": "Hi there! How can I help you?"
}
```

### `audio`

Audio response (TTS output or direct LLM audio).

```json
{
  "type": "audio",
  "audio_data": "<base64 encoded PCM 48kHz>"
}
```

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
