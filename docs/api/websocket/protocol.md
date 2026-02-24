# WebSocket Protocol

## Connection

Connect to the WebSocket endpoint:

```
ws://localhost:48911/ws/{lanlan_name}
```

Where `{lanlan_name}` is the character identifier (e.g., `小天`).

### Connection flow

1. Client opens WebSocket connection
2. Server assigns a session ID (UUID) and validates the character exists
3. Client sends `start_session` to initialize the LLM session
4. Client sends `stream_data` messages with audio or text input
5. Server streams responses back (text, audio, status, emotion)
6. Client sends `end_session` to close
7. Server performs hot-swap to a pre-warmed session

### Keep-alive

Send periodic `ping` messages to prevent connection timeout:

```json
{ "action": "ping" }
```

Server responds with:

```json
{ "type": "pong" }
```

## Session management

### Start session

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `action` | `"start_session"` | Required |
| `input_type` | `"audio"` \| `"text"` | Input mode |
| `new_session` | boolean | Whether to create a fresh session |

### End session

```json
{ "action": "end_session" }
```

Triggers the hot-swap mechanism: the current LLM session is closed and replaced with a pre-warmed one.

### Pause session

```json
{ "action": "pause_session" }
```

Keeps the WebSocket connected but pauses LLM processing.

## Error handling

If the character name is invalid, the server closes the WebSocket with an appropriate close code. If the character is switched server-side, the server sends a `catgirl_switched` message directing the client to reconnect.
