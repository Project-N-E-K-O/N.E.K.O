# LLMSessionManager

**Package:** `main_logic/core/` — the class is assembled in `core/manager.py` (`__init__` holds all instance state) from domain mixin modules (`context_append` / `focus` / `tts_runtime` / `turn` / `tool_calling` / `lifecycle` / `proactive` / `greeting` / `streaming` / `notify`). `core/__init__.py` preserves the supported `main_logic.core` compatibility surface for retained imports and monkeypatch targets; private helpers deliberately retired by later refactors are not re-exported as aliases.

The `LLMSessionManager` is the heart of N.E.K.O. — one instance per character, managing the entire conversation lifecycle.

## Responsibilities

- WebSocket connection management
- LLM session creation and hot-swapping
- TTS pipeline coordination
- Audio resampling (24kHz → 48kHz)
- Agent callback injection
- Translation support

## Key methods

### `start_session(websocket, new, input_mode)`

Initializes a new LLM session:

1. Creates an `OmniRealtimeClient` with the character's configuration
2. Connects to the Realtime API via WebSocket
3. Starts the TTS worker thread (if voice output is enabled)
4. Begins background preparation of the next session for hot-swap

### `stream_data(message)`

Processes incoming user input:

- **Audio**: Sends PCM audio chunks to the Realtime API client
- **Text**: Sends text messages to the LLM
- **Screen**: Sends screenshots for multi-modal understanding

### `handle_new_message()`

Called when the LLM produces output:

- Routes text output to the TTS queue (or directly to WebSocket)
- Sends emotion labels for expression mapping
- Handles agent notifications

### `end_session(by_server)`

Closes the current session and triggers hot-swap:

1. Closes the Realtime API WebSocket
2. Calls `_perform_final_swap_sequence()` for seamless transition
3. Flushes cached audio from the swap period

### `cleanup(expected_websocket)`

Releases all resources when the WebSocket disconnects.

### `trigger_agent_callbacks()`

Injects pending agent results into the next LLM conversation turn, allowing the character to reference agent findings.

### `_get_translation_service()`

Lazily initializes and returns a shared `TranslationService` (from `utils/language_utils.py`), which performs robust translation via `translate_text_robust()` / `translate_dict()` with a googletrans → translatepy → LLM fallback chain. Note: `LLMSessionManager` only exposes this accessor; there is no `translate_if_needed(text)` method. The actual "translate when the user's language differs from the character's language" behavior lives in the REST layer (e.g. `main_routers/characters_router.py`, which translates character profile fields), not on the session manager.

## Thread model

```
Main async loop (FastAPI)
  ├── WebSocket recv loop
  ├── LLM event handlers (on_text_delta, on_audio_delta)
  │
  ├── TTS worker thread (queue consumer)
  │
  └── Background session preparation (hot-swap)
```

## Integration points

- **WebSocket Router** → calls `start_session`, `stream_data`, `end_session`
- **Agent Event Bridge** → delivers results via `pending_agent_callbacks`
- **Config Manager** → provides character data and API configuration
- **TTS Client** → `get_tts_worker()` factory creates TTS workers
