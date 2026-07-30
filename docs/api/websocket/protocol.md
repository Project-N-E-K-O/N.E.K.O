# WebSocket Protocol

The main server exposes one application WebSocket route:

```text
ws://127.0.0.1:48911/ws/{lanlan_name}
```

URL-encode `{lanlan_name}`. Use `wss://` when a trusted reverse proxy terminates TLS. This is the bundled UI protocol, not a separately versioned public wire standard.

## Connection acceptance

1. The server accepts the WebSocket and validates the path character against the in-process session managers.
2. If the character is unknown, it may first send `catgirl_switched` with a valid fallback and then closes the socket. No custom close code is guaranteed.
3. For a valid character, the connection is installed on that character's session manager. The server assigns an internal UUID, but does not send that UUID as a connection-ack frame.
4. The client may use control actions such as `greeting_check` immediately. Conversation media requires `start_session` first.

Only the newest connection UUID for a character is authoritative in the router. If an older connection later sends a frame, it receives status code `CHARACTER_SWITCHING_TERMINAL` and is closed. First-party multi-window support therefore relies on the project's inter-page proxy/synchronization layer rather than independent competing primary sockets.

## Frame model

- Client commands are normally UTF-8 JSON text frames with top-level `action`.
- Client microphone audio may use the preferred binary frame: ASCII `NEKO`,
  then a little-endian uint32 sample rate (`16000` or `48000`), then mono
  little-endian signed PCM16. JSON `stream_data.data` arrays remain supported
  for compatibility.
- Server events are normally UTF-8 JSON text frames with top-level `type`.
- TTS audio is the exception: an `audio_chunk` JSON header is followed by one binary frame.
- Any client JSON object may include `language`; the router updates the character's current UI language before dispatching its `action`.

Malformed JSON is a connection-level error: the handler sends a best-effort `SERVER_ERROR` status, exits its receive loop, and cleans up the current session. Binary frames behave asymmetrically: a malformed `NEKO` binary frame is logged and dropped, and the connection stays open for the next frame.

## Session lifecycle

The WebSocket connection and provider session have separate lifetimes:

```text
socket open
    │
    ├─ voice_input_control(lease_sync) [recommended for audio]
    │
    ├─ start_session ─> lease authorized ─> session_preparing ─> session_started
    │                         │                                  └> session_failed
    │                         └> VOICE_INPUT_LEASE_REQUIRED
    │
    ├─ stream_data / avatar_interaction / control events
    │
    ├─ pause_session or end_session ─> provider session ends
    │                                  socket remains open
    │
    └─ socket close ─> current provider session and route-owned state clean up
```

### Start

```json
{
  "action": "start_session",
  "input_type": "audio",
  "new_session": false,
  "language": "en"
}
```

Accepted `input_type` values are `audio`, `screen`, `camera`, `text`, `avatar_drop_image`, and `user_image`. `text` and the two one-shot image types start the text/offline mode; `audio`, `screen`, and `camera` select the realtime/audio mode. `new_session` is a provider-session hint, not the WebSocket connection UUID.

New audio clients are strongly encouraged to first send a complete
`voice_input_control` snapshot with `event: "lease_sync"`. Its
`lease_generation` must increase monotonically within the current WebSocket and
restarts after reconnecting. Invalid or stale control messages produce
`VOICE_INPUT_CONTROL_REJECTED` and permanently disable legacy fallback for that
connection.

For compatibility, a connection that has sent no control message can acquire a
generation-0 Core lease immediately before its first ordinary audio session
starts. This does not run on the game route and cannot override an explicit
owner, hard mute, focus suppression, connection replacement, or newer lease
generation. If authorization fails, the server sends
`VOICE_INPUT_LEASE_REQUIRED` and does not start the Provider session.

Session startup runs asynchronously. Wait for the matching
`session_started.input_mode` before streaming microphone samples.
`session_preparing` is progress only, and `session_failed` means the requested
mode did not start. A `status` event often precedes a failure with the
machine-readable cause. `session_started` reports Provider readiness only; it
does not bypass MicLease checks.

When a game route is active, text/image inputs may be acknowledged or routed to the game controller, and an audio session can be used as the game's realtime STT provider. That behavior is part of the first-party game integration.

### Pause and end

```json
{ "action": "pause_session" }
```

`pause_session` marks the manager idle and ends the current provider session. It does not preserve a paused upstream stream.

```json
{ "action": "end_session", "reason": "user_stop" }
```

`end_session` schedules provider cleanup and leaves the application WebSocket available for a later start. Optional `goodbye_active: true` or `reason: "goodbye"` also enables the silent-goodbye gate. Neither action promises a pre-warmed replacement session.

The server can independently send `session_ended_by_server` after an upstream disconnect, configuration change, or timeout.

## Keep-alive

The protocol supports an application heartbeat:

```json
{ "action": "ping" }
```

```json
{ "type": "pong" }
```

Choose the interval according to the client/proxy timeout. The backend does not require a particular interval in the handler itself.

## Status and errors

Status is a nested JSON envelope for historical frontend compatibility:

```json
{
  "type": "status",
  "message": "{\"code\":\"INVALID_INPUT_TYPE\",\"details\":{\"input_type\":\"file\"}}"
}
```

Parse `message` once more as JSON. Known examples include
`INVALID_INPUT_TYPE`, `UNKNOWN_ACTION`, `SERVER_ERROR`,
`VOICE_INPUT_CONTROL_REJECTED`, `VOICE_INPUT_LEASE_REQUIRED`,
provider/auth/quota codes, and `CHARACTER_SWITCHING_TERMINAL`. The set can grow;
clients should provide a generic fallback for unknown codes.

Unknown actions do not close the socket: they produce `UNKNOWN_ACTION`. In contrast, invalid JSON, superseded connections, character deletion/rename, or transport disconnect lead to cleanup.

## Security boundary

The route has no standalone authentication handshake. The application assumes a local trusted UI and uses user-controlled text, image, telemetry, and capture metadata. Keep port 48911 on loopback or place an authenticated, origin-restricted proxy in front of it. Never treat telemetry dimensions, local capture responses, or character names as trusted input.

See [Message Types](./message-types) and [Audio Streaming](./audio-streaming).
