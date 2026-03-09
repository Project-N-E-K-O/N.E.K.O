# LLMSessionManager

**File:** `main_logic/core.py` (~2460 lines)

The `LLMSessionManager` is the heart of N.E.K.O. — one instance per character, managing the entire conversation lifecycle.

## Responsibilities

- WebSocket connection management
- LLM session creation and hot-swapping
- TTS pipeline coordination
- Audio resampling (24kHz → 48kHz via soxr)
- Agent callback injection
- Proactive message delivery
- Screenshot request/response
- Translation support

## Key methods

### Session lifecycle

### `start_session(websocket, new, input_mode)`

Initializes a new LLM session:

1. Creates an `OmniRealtimeClient` (or `OmniOfflineClient`) with the character's configuration
2. Connects to the Realtime API via WebSocket
3. Starts the TTS worker thread (if voice output is enabled)
4. Begins background preparation of the next session for hot-swap

### `stream_data(message)`

Processes incoming user input:

- **Audio**: Sends PCM audio chunks to the Realtime API client
- **Text**: Sends text messages to the LLM
- **Screen/Camera**: Sends screenshots for multi-modal understanding

### `end_session(by_server)`

Closes the current session and triggers hot-swap:

1. Closes the Realtime API WebSocket
2. Calls `_perform_final_swap_sequence()` for seamless transition
3. Flushes cached audio from the swap period

### `cleanup(expected_websocket)`

Releases all resources when the WebSocket disconnects.

### Callback handlers

| Handler | Purpose |
|---------|---------|
| `handle_new_message()` | Routes LLM output to TTS or WebSocket |
| `handle_text_data(text, is_first_chunk)` | Processes streamed text chunks |
| `handle_audio_data(audio_data)` | Processes streamed audio output |
| `handle_response_complete()` | Called when a full LLM response is finished |
| `handle_response_discarded(reason, ...)` | Called when a response is discarded (repetition, etc.) |
| `handle_input_transcript(transcript)` | User's speech-to-text result |
| `handle_output_transcript(text, is_first_chunk)` | LLM output as text (parallel to audio) |
| `handle_silence_timeout()` | Fires when the user goes silent |
| `handle_connection_error(message)` | LLM connection failure handler |
| `handle_repetition_detected()` | Triggered when repetition is detected |

### Agent integration

| Method | Purpose |
|--------|---------|
| `trigger_agent_callbacks()` | Deliver pending agent results to the LLM turn |
| `enqueue_agent_callback(callback)` | Queue an agent result for injection |
| `drain_agent_callbacks_for_llm()` | Collect all queued callbacks as LLM context |
| `update_agent_flags(flags)` | Update agent capability flags |

### Proactive messaging

| Method | Purpose |
|--------|---------|
| `deliver_text_proactively(...)` | Push a character-initiated message |
| `prepare_proactive_delivery(min_idle_secs)` | Check idle time and prepare delivery |
| `feed_tts_chunk(text)` | Feed a text chunk to TTS during proactive delivery |
| `finish_proactive_delivery(full_text)` | Complete proactive message with full text |

### Screenshot mechanism

| Method | Purpose |
|--------|---------|
| `request_fresh_screenshot(timeout)` | Request a screenshot from the frontend |
| `resolve_screenshot_request(b64)` | Handle the frontend's screenshot response |

### Hot-swap internals

| Method | Purpose |
|--------|---------|
| `_perform_final_swap_sequence()` | Execute the hot-swap transition |
| `_background_prepare_pending_session()` | Pre-warm the next session in background |
| `_flush_hot_swap_audio_cache()` | Flush cached audio from the swap period |

### `translate_if_needed(text)`

Translates text when user language differs from character language.

## Thread model

```
Main async loop (FastAPI)
  ├── WebSocket recv loop
  ├── LLM event handlers (on_text_delta, on_audio_delta, on_interrupt, ...)
  │
  ├── TTS worker thread (queue consumer)
  │
  └── Background session preparation (hot-swap)
```

## Integration points

- **WebSocket Router** → calls `start_session`, `stream_data`, `end_session`
- **Agent Event Bus** → delivers results via `enqueue_agent_callback`
- **Config Manager** → provides character data and API configuration
- **TTS Client** → `get_tts_worker()` factory creates TTS workers
- **Cross Server** → forwards messages to Monitor and Memory servers
