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
| `is_hot_swap_imminent` | bool | Final swap currently in progress |
| `is_preparing_new_session` | bool | Background preparation underway |
| `message_cache_for_new_session` | list | Accumulates conversation during prep |
| `pending_session_warmed_up_event` | Event | Signals background preparation complete |
| `background_preparation_task` | Task | Asyncio task for background prep |
| `final_swap_task` | Task | Asyncio task for atomic swap |

## Hot-swap mechanism

The hot-swap system ensures zero-downtime session transitions with a ~40-second cycle:

```
0-40s:  Active session handling turns
  40s:  Uptime threshold reached → memory archive triggered
  50s:  Summary complete + 10s delay → Background prep starts
        • Prepare new client (OmniRealtimeClient or OmniOfflineClient)
        • Warm up TTS worker if needed
        • Set pending_session_warmed_up_event
  50s+: End of current turn + new session ready → Final swap
        • Close old session
        • Activate new session
        • Flush hot-swap audio cache with rate-limiting (5× chunk multiplier)
        • Resume conversation seamlessly
```

1. **Prepare**: While the current session handles user input, a new session is created in the background with the latest character configuration.

2. **Cache**: When `end_session()` is called, any in-flight audio output is stored in `hot_swap_audio_cache`.

3. **Swap**: `_perform_final_swap_sequence()` atomically replaces the old session with the new one.

4. **Flush**: Cached audio is sent to the client with rate-limiting, providing a seamless transition.

This means the character can update its personality, voice, or model settings between conversation turns without the user experiencing any delay.

## Callback handlers

The `LLMSessionManager` provides a rich set of callback handlers for session events:

| Handler | Purpose |
|---------|---------|
| `handle_new_message()` | Clears TTS queue, resets resampler state, generates new `speech_id` |
| `handle_text_data(text, is_first_chunk)` | Sends text to frontend + enqueues TTS request |
| `handle_audio_data(audio_bytes)` | Resamples (24→48kHz) and streams to frontend |
| `handle_input_transcript(transcript)` | User activity tracking + message cache update |
| `handle_output_transcript(text, is_first_chunk)` | Display text + TTS enqueue |
| `handle_response_complete()` | TTS signal, turn end, hot-swap trigger evaluation |
| `handle_response_discarded()` | Clear TTS pipeline, notify frontend |
| `handle_silence_timeout()` | Auto-close on 90s silence (GLM/Free API only) |

## Audio processing

Audio flows through a stateful resampling pipeline:

```
LLM output (24kHz PCM) ──> soxr ResampleStream ──> 48kHz PCM ──> base64 ──> WebSocket
```

The resampler uses `soxr` `ResampleStream` which maintains internal state across chunks to prevent discontinuities at chunk boundaries. The resampler state is reset at each new message (`handle_new_message()`) to avoid cross-message artifacts.

## Agent integration

The session manager coordinates with the agent system through callbacks:

1. Agent results arrive via ZeroMQ on the `MainServerAgentBridge`
2. Results are dispatched to the relevant `LLMSessionManager` via `pending_agent_callbacks`
3. `trigger_agent_callbacks()` injects agent results into the next LLM conversation turn
4. The LLM can then reference agent findings in its response to the user

## Proactive message batching

Consecutive unsynced assistant messages are merged before memory sync. The system distinguishes between user-driven and proactive (agent-initiated) responses, ensuring proper attribution in memory storage.

## Translation support

`translate_if_needed()` provides automatic translation when the user's language differs from the character's configured language. This uses the `TranslationService` which falls back through googletrans → translatepy → LLM-based translation.
