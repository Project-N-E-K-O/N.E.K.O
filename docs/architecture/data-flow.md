# Data Flow

## Browser and Electron entry points

The Main Server exposes one character WebSocket at `/ws/{lanlan_name}`. Both the development page (`/`, backed by `index.html`) and Electron chat windows use the same backend protocol and the same React chat implementation from `frontend/react-neko-chat/`. Electron routes such as `/chat` may proxy the socket through window IPC, but the message contract is unchanged.

The obsolete `#chat-container` DOM implementation is not part of the live data path. Legacy `appendMessage()` calls are intercepted by `static/app/app-chat-adapter.js` and routed to the React chat component.

## WebSocket lifecycle

```text
Client                         Main Server                    LLM client
  │                                │                              │
  │── connect /ws/{name} ─────────>│ accept + bind manager        │
  │── start_session(audio|text) ──>│── create/connect ───────────>│
  │<─ session_preparing / started ─│                              │
  │── stream_data ────────────────>│── text or PCM input ────────>│
  │<─ gemini_response deltas ──────│<─ text/output callbacks ─────│
  │<─ audio_chunk header ──────────│<─ native or external TTS ────│
  │<─ binary PCM frame ────────────│                              │
  │<─ system: turn end ────────────│<─ response completion ───────│
  │── end_session ────────────────>│── cancel/close/cleanup ─────>│
```

`start_session` is asynchronous. Clients must wait for `session_started` and handle `session_failed`; `session_preparing` marks the quiet startup window. Audio mode creates an `OmniRealtimeClient`, while text mode creates an `OmniOfflineClient`.

`stream_data` may arrive while startup or a cross-mode rebuild is in progress. The manager caches ordered text/image input and serializes audio through a bounded queue instead of assuming every frame can be sent immediately.

## Client → server messages

All control messages are JSON text frames. Common chat messages are:

```json
{ "action": "start_session", "input_type": "audio", "new_session": true }
{ "action": "start_session", "input_type": "text" }
{ "action": "stream_data", "input_type": "audio", "data": [0, 12, -8] }
{ "action": "stream_data", "input_type": "text", "data": "Hello" }
{ "action": "stream_data", "input_type": "image", "data": "data:image/png;base64,..." }
{ "action": "end_session" }
{ "action": "ping" }
```

The audio payload is an array of signed PCM samples, not a base64 string. The browser capture path normally sends 48 kHz mono chunks; the realtime session path preprocesses them and sends 16 kHz audio upstream.

Other actions share the socket, including `greeting_check`, `avatar_interaction`, `screenshot_response`, `capture_bridge_*`, `goodbye_state`, `language_update`, `voice_play_start`, `voice_play_end`, and telemetry. Unknown actions produce a structured `status` error.

Only one connection generation is current for a character. A newer connection replaces the stored session ID; stale sockets are closed and cannot continue writing to the manager.

## Server → client messages

Important JSON message types include:

| Type | Purpose |
|---|---|
| `session_preparing`, `session_started`, `session_failed` | Session startup state with `input_mode` |
| `gemini_response` | Streaming assistant text; the historical name is retained across providers |
| `user_transcript` | Recognized user speech |
| `audio_chunk` | Header containing `speech_id`; the next WebSocket frame is binary PCM |
| `system` with `turn end` or `turn end agent_callback` | Turn completion |
| `status` | Structured status or error payload |
| `expression`, `focus_state`, `focus_charge` | Character presentation state |
| `agent_status_update`, `agent_task_update`, `agent_notification` | Agent state and task delivery |
| `request_screenshot`, `capture_bridge_request` | Client capture requests |
| `session_ended_by_server`, `reload_page`, `catgirl_switched` | Lifecycle recovery or character changes |
| `pong` | Heartbeat response |

External TTS audio is normalized by provider workers to 48 kHz mono PCM and sent as an `audio_chunk` JSON header followed by a binary frame. It is not embedded in JSON as `audio_data`.

## REST request flow

```text
Browser / Electron renderer
        │
        ├─ /api/characters/* ──> character routers ──> ConfigManager
        ├─ /api/agent/* ───────> agent_router ───────> Agent Server :48915
        └─ other /api/* ───────> domain router ──────> shared-state getters
```

Routers obtain long-lived managers through `main_routers/shared_state.py` getters. Agent control endpoints are Main Server proxies; browsers do not need direct access to port 48915.

## Agent event flow

```text
cross_server turn/session end
        │ recent messages + current-turn attachments
        ▼
MainServerAgentBridge ── ZMQ :48963 ──> AgentServerEventBridge
        ▲                                      │
        │ ACK / task_update / task_result      ▼
        └──────────── ZMQ :48962 ───── channel dispatch
        │
        ├─ task_update ──> WebSocket ──> Task HUD
        └─ task_result ──> LLMSessionManager callback queue ──> chat delivery
```

Session broadcasts use the separate PUB/SUB address on port 48961. Analyze requests use the reliable PUSH/PULL queue on port 48963 and are acknowledged over the Agent → Main channel on port 48962.

## TTS data flow

Text and audio are not always synthesized through the same path:

- Text sessions always feed the project TTS runtime unless TTS is disabled, in which case the dummy worker produces no audio.
- Voice sessions use provider-native realtime audio when the selected voice supports it. Custom voices and selected external providers use the project TTS runtime instead.
- Project TTS workers receive text through a thread queue and return 48 kHz PCM through a response queue. `send_speech()` emits the `speech_id` header and binary bytes.

See [Session Management](/architecture/session-management) and [TTS Pipeline](/architecture/tts-pipeline) for the concurrency and interruption rules behind these flows.
