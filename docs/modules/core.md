# LLMSessionManager

**Package:** `main_logic/core/`

`LLMSessionManager` is the per-character coordinator between the browser WebSocket, one active model client, memory, tools, proactive results, and speech output. `app/main_server/character_runtime.py` creates and retains the manager for each character.

The class is assembled in `manager.py`. Its `__init__` is the single owner of instance state; methods are supplied by `context_append`, `focus`, `tts_runtime`, `turn`, `tool_calling`, `lifecycle`, `proactive`, `greeting`, `streaming`, and `notify` mixins. `main_logic.core` re-exports only the supported compatibility surface of the former monolithic module.

## Session selection and startup

```python
await manager.start_session(websocket, new=False, input_mode="audio")
```

`start_session()` is serialized and guarded against duplicate or cross-mode starts. A user-initiated request can replace an in-flight start of the other mode; background greeting/proactive starts do not override it.

The startup phases are:

1. bind the frontend WebSocket, reload runtime, character, voice, and provider settings, and notify the frontend that preparation started;
2. retire the previous active/pending session and reset input/TTS state;
3. prefetch `GET /new_dialog/{lanlan_name}` from the Memory Server;
4. start external TTS when the selected route needs it;
5. create `OmniOfflineClient` for text input or `OmniRealtimeClient` for audio input, attach callbacks/tools, and connect it with the rendered memory context;
6. promote the connected client to `self.session`, start its receive task where required, flush queued context/input, and acknowledge readiness.

Memory context is a required startup dependency. Connection, timeout, and non-2xx failures from `/new_dialog` fail the session start; the manager does not silently start with an empty context.

Repeated start failures open a local circuit so every incoming audio chunk cannot trigger another connection attempt. An explicit frontend retry resets that circuit.

## Input routing

```python
await manager.stream_data(message)
```

`stream_data()` is the WebSocket router's media entry point. It queues eligible input while a session is starting and can create or rebuild the client when the message type requires a different mode.

- text input is sent to `OmniOfflineClient.stream_text()`;
- audio bytes are sent to `OmniRealtimeClient.stream_audio()` through a bounded audio queue;
- screenshots, camera frames, and other supported visual inputs are processed and passed to the current client's image path;
- live vision frames are dropped when no suitable active session exists instead of implicitly starting one.

Text and audio sessions are explicit alternatives. A realtime transport error is not converted into an offline session without a new mode/start decision.

## Output and turn lifecycle

The model clients call manager methods rather than writing to the frontend directly:

| Callback | Responsibility |
|---|---|
| `handle_new_message()` | At a realtime user-turn boundary, interrupt stale speech, reset resampling/TTS state, and rotate the `speech_id` |
| `handle_text_data()` | Stream assistant text to the UI and, when external TTS is active, to the TTS request queue |
| `handle_audio_data()` | Forward native model PCM; the manager resamples 24 kHz PCM to frontend 48 kHz PCM |
| `handle_input_transcript()` | Record voice input, update activity/language state, publish user context, and coordinate memory/agent mirrors |
| `handle_output_transcript()` | Mirror assistant transcript and maintain the turn text used by downstream systems |
| `handle_response_complete()` | Finish TTS, emit turn-end to WebSocket and sync queue, then run archive/prewarm and callback-delivery decisions |

Every reply is tagged with a `speech_id`. Interruption and proactive-race guards use it to discard late TTS or model chunks from a superseded turn.

`end_session()` closes active and pending clients, cancels receive/preparation work, resets stream state, and may complete a prepared hot swap. `cleanup(expected_websocket=...)` adds ownership guards so a stale WebSocket disconnect cannot tear down a newer connection. `shutdown()` is the synchronous finalizer for long-lived manager tasks when the character runtime replaces an inactive manager.

## Hot swap and context append

Hot-swap preparation is driven by turn/token thresholds, renew signals, or queued extra context; it is not started unconditionally for every new session. `_background_prepare_pending_session()` builds a connected pending client, and `_perform_final_swap_sequence()` promotes it under locks while preserving input/context that arrived during the transition.

`append_context()` is the public path for durable context injection. It deduplicates requests, targets the active and/or pending client, and queues undeliverable context for the next startup prompt. This is distinct from model-visible proactive messages, which use `submit_proactive_callback()` / `enqueue_agent_callback()` and are delivered by `trigger_agent_callbacks()` under response and voice-playback gates.

## Tools and memory

The manager owns a `ToolRegistry`. `register_tool_and_sync()` and the corresponding unregister/clear methods update active client tool definitions. `_register_builtin_tools()` installs `recall_memory` for both offline and realtime sessions.

Memory has two model-facing paths:

1. **Automatic new-dialog context** — startup and hot-swap fetch `GET /new_dialog/{lanlan_name}`. The Memory Server renders persona, usable reflections, recent compressed history, and time-sensitive context into the initial prompt.
2. **On-demand recall** — `recall_memory` sends a natural-language `query`, a `time` expression, or both to `POST /query_memory/{lanlan_name}`. It searches facts and reflections; persona is excluded because it is already rendered automatically.

Transcript persistence is coordinated separately by `main_logic/cross_server.py`: incremental content uses `/cache`, and turn/session boundaries use `/process`, `/renew`, or `/settle`. Persisted facts and reflections are not all injected automatically.

## Execution boundaries

```text
Main Server asyncio loop
  ├─ frontend WebSocket receive/send
  ├─ LLMSessionManager lifecycle, client callbacks, and tool/proactive tasks
  ├─ active-client receive task and optional pending-session preparation
  ├─ per-character cross-server sync connector asyncio task
  └─ TTS response-handler asyncio task

Dedicated TTS worker thread
  └─ synchronous provider worker using request/response queues
```

Async locks protect session replacement, frontend writes, caches, and media queues. The TTS worker is the main thread boundary inside this manager; blocking queue waits and selected filesystem/SDK operations are offloaded with `asyncio.to_thread()`.

## Primary callers and dependencies

- `main_routers/websocket_router.py` calls `start_session()`, `stream_data()`, `end_session()`, and guarded `cleanup()`.
- `app/main_server/character_runtime.py` owns per-character managers, WebSocket locks, sync queues, and cross-server connector tasks.
- `ConfigManager` supplies character, API, voice, and response-limit settings.
- `OmniOfflineClient` / `OmniRealtimeClient` supply the model transport.
- The Memory Server is required for initial context; the Agent bridge and proactive delivery paths provide optional background results.
- `main_logic/tts_client/` supplies the external TTS worker function; the manager owns its thread and queues.
