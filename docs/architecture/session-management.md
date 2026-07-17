# Session Management

`LLMSessionManager` is the per-character coordinator for chat transport, LLM clients, TTS, hot-swap, tool callbacks, and proactive delivery. Its class is assembled in `main_logic/core/manager.py`; domain methods live in mixins such as `lifecycle.py`, `streaming.py`, `turn.py`, `tts_runtime.py`, and `proactive.py`.

## Ownership model

The Main Server keeps one manager per loaded character. A manager can outlive an individual browser connection, but it stores the current WebSocket and only the newest connection generation may control it. On disconnect, cleanup is guarded with the expected WebSocket so a stale socket cannot tear down a replacement connection.

The manager owns two different LLM clients:

| Input mode | Client | Input and output |
|---|---|---|
| `text` | `OmniOfflineClient` | Text/images in; streamed text out; project TTS supplies speech |
| `audio` | `OmniRealtimeClient` | Realtime PCM and transcripts; native audio or project TTS depending on voice routing |

Changing between text and audio is a rebuild, not an in-place mode toggle.

## Startup state machine

```text
start_session requested
        │
        ├─ serialize concurrent starts / wait for cross-mode start
        ├─ reload model and voice configuration
        ├─ mark session_ready = false
        ├─ fetch memory context and build initial prompt
        ├─ construct the mode-specific client locally
        ├─ connect, bind guarded callbacks, sync tools
        ├─ compare-and-set client into self.session
        ├─ start or reuse external TTS when required
        └─ flush pending input; emit session_started
```

The frontend first receives `session_preparing`. Success produces `session_started`; failure produces `session_failed` and closes partially created resources.

Memory context is a startup dependency, not an optional empty-context fallback. If the Memory Server request fails, startup raises a connection error, enters the normal failure cleanup path, and contributes to the retry/circuit-breaker count.

`_starting_session_count` and `_starting_input_mode` protect the async startup window. A request for the other mode waits for the in-flight start to settle, then performs a serialized teardown and restart. The manager also uses compare-and-set promotion so a late startup cannot overwrite a newer winning session.

After three consecutive startup failures, the session-start circuit opens. Internal recovery stops retrying until a user-initiated `start_session` clears it. A short cooldown also prevents rapid retry loops.

## Input ordering and backpressure

Input is accepted before the upstream client is fully ready:

- Ordered text and image messages are held in `pending_input_data` under `input_cache_lock`, then flushed after activation.
- Audio messages pass through an `asyncio.Queue` with a maximum of 300 entries. If it is full, the oldest entry is dropped so the event loop and microphone capture cannot grow memory without bound.
- Incoming 48 kHz microphone chunks are noise-reduced and converted to the 16 kHz format expected by the realtime upstream when the audio processor is active.
- Each stream operation snapshots the session and audio epoch. Data is discarded if teardown or replacement occurs while preprocessing is in progress.

These guards are why callers should use `stream_data()` rather than write directly to `self.session`.

## Session renewal and hot-swap

Hot-swap renews a long-running conversation without dropping context or microphone input:

1. The manager prewarms a mode-matched pending client in the background with the next context snapshot.
2. At the renewal boundary it primes the pending client with final context and a bounded selection of queued Agent/event callbacks.
3. Audio arriving while promotion is imminent is stored in `hot_swap_audio_cache` after preprocessing.
4. The old listener is cancelled and awaited. The pending client is then promoted atomically to `self.session`.
5. Cached 16 kHz input audio is flushed to the new realtime client in bounded chunks.

The cache contains **user input audio during the swap**, not assistant output. `end_session()` is teardown; it does not itself mean “swap to a prewarmed session.” Failed or cancelled promotion closes the pending resources and preserves callbacks that were not safely consumed.

## TTS ownership

The manager owns a request `Queue`, a response `Queue`, one daemon provider thread, and an async response handler when the project TTS path is active. Text sessions select this path. Audio sessions may instead request native audio from the realtime provider.

TTS readiness and pending text are protected separately from session readiness. A live compatible worker can be reused across session starts; a changed provider, voice, endpoint, model, or credential produces a new runtime identity and worker.

Audio from project TTS is delivered as 48 kHz mono PCM. Resampling occurs inside the provider worker according to that provider's source rate; it is not a universal post-step inside `LLMSessionManager`.

## Agent and proactive delivery

Agent results arrive through the Main Server's ZeroMQ bridge and are placed in `pending_agent_callbacks` for the matching character. Delivery depends on mode and activity:

- Text mode can start a controlled proactive text turn when the state machine is idle.
- Voice mode attempts supported manual injection; otherwise callbacks remain queued for the next hot-swap prime.
- Actual frontend `voice_play_start` / `voice_play_end` signals gate proactive speech so generation completion is not mistaken for playback completion.
- Goodbye mode and takeover controllers can defer or suppress ordinary local delivery without losing the queue.

Delivery is locked and token-budgeted. Items that do not fit remain queued for a later turn, and websocket reconnect schedules another delivery attempt.

## Teardown

`end_session()` and `cleanup()` cancel and await listeners, startup/swap tasks, TTS handlers, and pending resources before clearing references. The teardown path snapshots resource identities so it does not accidentally close a worker or client recreated by a concurrent start.

WebSocket disconnect calls `cleanup(expected_websocket=...)`. Server-driven session closure additionally notifies the frontend with `session_ended_by_server` when appropriate.

## Translation is a separate service

Subtitle and profile translation is implemented in `utils/language_utils.py` and exposed through routes such as `/translate`; it is not an `LLMSessionManager` stage.

Fallback depends on region:

- China region: `translatepy` services reachable from mainland China, then LLM translation.
- Other regions: Google Translate, then LLM translation. After Google is marked unavailable, later calls skip it.
- If every backend fails, the original text is returned.

## Implementation map

| Concern | File |
|---|---|
| Attribute ownership and mixin assembly | `main_logic/core/manager.py` |
| Start, hot-swap, end, cleanup | `main_logic/core/lifecycle.py` |
| Input cache and audio streaming | `main_logic/core/streaming.py` |
| Turn completion and interruption | `main_logic/core/turn.py` |
| TTS worker lifecycle | `main_logic/core/tts_runtime.py` |
| Agent/proactive callback delivery | `main_logic/core/proactive.py` |
| Browser WebSocket dispatch | `main_routers/websocket_router.py` |
