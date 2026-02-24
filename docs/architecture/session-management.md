# Session Management

The `LLMSessionManager` class in `main_logic/core.py` is the central coordinator for each character's conversation sessions. Each character has its own manager instance.

## Session lifecycle

```
new connection ──> start_session() ──> stream_data() ──> end_session()
                        │                                      │
                        │                               hot-swap to
                        │                               pre-warmed session
                        │
                   Creates OmniRealtimeClient
                   Starts TTS worker thread
                   Prepares next session (background)
```

## Key attributes

| Attribute | Type | Purpose |
|-----------|------|---------|
| `websocket` | WebSocket | Current client connection |
| `lanlan_name` | str | Character identifier |
| `session` | OmniRealtimeClient | Current LLM session |
| `is_active` | bool | Whether session is running |
| `input_mode` | str | `"audio"` or `"text"` |
| `voice_id` | str | Character's TTS voice ID |
| `tts_request_queue` | Queue | Outgoing TTS requests |
| `tts_response_queue` | Queue | Incoming TTS audio |
| `agent_flags` | dict | Agent capability flags |
| `hot_swap_audio_cache` | list | Audio buffered during swap |

## Hot-swap mechanism

The hot-swap system ensures zero-downtime session transitions:

1. **Prepare**: While the current session handles user input, a new `OmniRealtimeClient` session is created in the background with the latest character configuration.

2. **Cache**: When `end_session()` is called, any in-flight audio output is stored in `hot_swap_audio_cache`.

3. **Swap**: `_perform_final_swap_sequence()` atomically replaces the old session with the new one.

4. **Flush**: Cached audio is sent to the client, providing a seamless transition.

This means the character can update its personality, voice, or model settings between conversation turns without the user experiencing any delay.

## Audio processing

Audio flows through a resampling pipeline:

```
LLM output (24kHz PCM) ──> soxr resampler ──> 48kHz PCM ──> base64 ──> WebSocket
```

The resampler uses `soxr` (high-quality sample rate conversion) to convert from the LLM's native 24kHz output to the browser's expected 48kHz playback rate.

## Agent integration

The session manager coordinates with the agent system through callbacks:

1. Agent results arrive via ZeroMQ on the `MainServerAgentBridge`
2. Results are dispatched to the relevant `LLMSessionManager` via `pending_agent_callbacks`
3. `trigger_agent_callbacks()` injects agent results into the next LLM conversation turn
4. The LLM can then reference agent findings in its response to the user

## Translation support

`translate_if_needed()` provides automatic translation when the user's language differs from the character's configured language. This uses the `TranslationService` which falls back through googletrans → translatepy → LLM-based translation.
