# Data Flow

## WebSocket chat lifecycle

This is the primary interaction flow — a user chatting with an AI character.

```
Browser                    Main Server                   LLM Provider
  │                            │                              │
  │──── WS connect ───────────>│                              │
  │     /ws/{lanlan_name}      │                              │
  │                            │                              │
  │──── start_session ────────>│                              │
  │     {input_type: "audio"}  │──── WS connect ─────────────>│
  │                            │     (OmniRealtimeClient)     │
  │                            │                              │
  │──── stream_data ──────────>│──── send_audio ─────────────>│
  │     {audio chunks}         │                              │
  │                            │<──── on_text_delta ──────────│
  │<──── {type: "text"} ──────│                              │
  │                            │<──── on_audio_delta ─────────│
  │<──── {type: "audio"} ─────│     (resampled 24→48kHz)     │
  │                            │                              │
  │──── end_session ──────────>│──── close ───────────────────│
  │                            │                              │
  │                            │── hot-swap to next session ──│
```

### Message format

**Client → Server (JSON text frames):**

```json
{ "action": "start_session", "input_type": "audio", "new_session": true }
{ "action": "stream_data", "input_type": "audio", "data": "<base64 PCM>" }
{ "action": "stream_data", "input_type": "text", "data": "Hello!" }
{ "action": "end_session" }
{ "action": "pause_session" }
{ "action": "screenshot_response", "data": "<base64 image>" }
{ "action": "ping" }
```

**Server → Client (JSON text frames):**

```json
{ "type": "text", "text": "Hi there!" }
{ "type": "audio", "audio_data": "<base64 PCM 48kHz>" }
{ "type": "status", "message": "Session started" }
{ "type": "emotion", "emotion": "happy" }
{ "type": "agent_notification", "text": "...", "source": "...", "status": "..." }
{ "type": "catgirl_switched", "data": { ... } }
{ "type": "pong" }
```

## REST API request flow

```
Browser ──── GET /api/characters/ ────> FastAPI Router
                                            │
                                            ├── shared_state (global session managers)
                                            ├── config_manager (character data)
                                            └── Response (JSON)
```

All REST endpoints follow standard FastAPI patterns. Routers access global state through `shared_state.py` getter functions to avoid circular imports.

## Agent task flow

```
LLMSessionManager                  Agent Server
  │                                    │
  │── ZMQ PUB (analyze request) ──────>│
  │                                    │── DirectTaskExecutor:
  │                                    │   parallel assess:
  │                                    │   ├── _assess_mcp()
  │                                    │   ├── _assess_browser_use()
  │                                    │   ├── _assess_computer_use()
  │                                    │   └── _assess_user_plugin()
  │                                    │   priority select & execute
  │                                    │── Analyzer: evaluate results
  │<── ZMQ PUSH (task_result) ────────│
  │                                    │
  │── inject into next LLM turn ──>   │
```

## TTS pipeline

```
LLM text output ──> TTS request queue ──> TTS worker thread
                                              │
                                     ┌────────┼──────────────┐
                                     │        │              │
                                     ▼        ▼              ▼
                                CosyVoice  GPT-SoVITS   StepFun RT
                               (DashScope) (Local)      (WebSocket)
                                     │        │              │
                                     └────────┼──────────────┘
                                              │
                                    TTS response queue
                                              │
                                    soxr ResampleStream (24→48kHz)
                                        (stateful, per-message)
                                              │
                                    WebSocket send to browser
```

The TTS pipeline is fully interruptible — when the user starts speaking (interrupt event), pending TTS output is discarded immediately. The `soxr` resampler maintains internal state across chunks to prevent discontinuities at chunk boundaries, and resets its state at each new message to avoid artifacts.
