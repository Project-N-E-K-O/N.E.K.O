# TTS Pipeline

N.E.K.O. has two speech-output paths: provider-native audio from an `OmniRealtimeClient`, and the project's external TTS runtime. The queue-and-worker pipeline described here is the second path; it is not used for every voice session.

## Path selection

`LLMSessionManager._resolve_session_use_tts()` decides whether to start the external runtime.

| Situation | Speech path |
|---|---|
| Text session | External TTS runtime |
| Voice session with a supported provider-native voice | Realtime provider native audio |
| Voice session with a custom/cloned voice or selected external provider | External TTS runtime |
| Livestream route using the free realtime service | Server-native audio |
| `DISABLE_TTS` | Dummy worker; no synthesized audio |

Voice routing is based on structured voice source/provider/reference data, current model configuration, and stored clone metadata. A voice ID alone is not globally unique across providers.

## External runtime

```text
LLM text delta
    │ normalize/strip text according to provider class
    ▼
thread-safe request Queue
    │ (speech_id, text) / control sentinels
    ▼
provider worker thread
    │ provider protocol + source-rate conversion
    ▼
thread-safe response Queue ──> async response handler
                                  │
                                  ├─ JSON {type: "audio_chunk", speech_id}
                                  └─ following WebSocket binary PCM frame
```

The queues and daemon thread are owned per `LLMSessionManager`. Provider workers share the callable contract `(request_queue, response_queue, api_key, voice_id)` even though their network protocols differ.

There are two broad worker classes:

- `ws_bistream`: streams text fragments and audio over a long-lived WebSocket. Qwen, Step, and CosyVoice are examples. Text fragments bypass CJK whitespace normalization so client-side buffering does not disturb provider cadence.
- `http_sentence`: segments cleaned text into synthesis requests. OpenAI, Gemini, MiniMax, MiMo, Doubao, and similar workers use this pattern.

Markdown markers and bracketed stage directions are stripped from spoken text for both classes.

## Provider dispatch

`main_logic/tts_client.get_tts_worker()` first asks `utils/tts/provider_registry.py` for the first selected special provider in priority order, then falls back to native/core defaults.

Current registered special routes include:

| Priority | Provider | Selection |
|---:|---|---|
| 10 | GPT-SoVITS | Enabled local custom TTS |
| 20 | vLLM-Omni | Explicit endpoint/model selection |
| 30 | MiniMax | Clone metadata |
| 40 | ElevenLabs | Clone or designed voice metadata |
| 50 | CosyVoice | Clone metadata |
| 60 | MiMo | Selected preset or clone metadata |
| 65 | Doubao TTS | Clone metadata |

If no special route wins, core-provider defaults cover Qwen, the free service, Step, CogTTS/GLM, Gemini, OpenAI, and Grok. An unsupported native combination falls back to the dummy worker rather than silently calling an unrelated provider.

Selection and credentials are resolved together. In particular, provider-specific API-key overrides prevent a worker from accidentally inheriting credentials from a different TTS configuration slot.

## Streaming and completion

LLM deltas are tagged with a `speech_id`. If a provider worker is not ready, text is held in `tts_pending_chunks`. The worker emits `__ready__`; the async handler then flushes the pending text in order.

Control items have distinct meanings:

| Item | Meaning |
|---|---|
| `(speech_id, text)` | Synthesize text for the current utterance |
| `(None, None)` | Finish and flush the current utterance; keep the worker alive |
| `("__interrupt__", None)` | Stop the current synthesis and mute late provider callbacks |
| `("__shutdown__", None)` | Exit the worker thread |

The done marker is deferred until both worker readiness and pending-text flush are satisfied. A speech-ID check prevents an old turn's delayed done marker from terminating a newer turn.

## Interruption

Interruption is initiated by the session/turn handling path when new user activity preempts current speech; it is not a generic provider `on_interrupt` callback.

`_clear_tts_pipeline()`:

1. drains queued response audio;
2. sends `__interrupt__` to a live worker;
3. resets text normalizer and stripper state;
4. waits briefly for the worker to mute its callbacks;
5. drains any late audio and clears pending text.

The frontend also receives user-activity data containing the interrupted `speech_id`, allowing playback to stop the correct utterance. Worker liveness, not `use_tts`, gates interruption because mirror-speech features can keep an external worker alive even during a native-audio session.

## Audio contract and backpressure

Workers convert provider output to mono, signed 16-bit little-endian PCM at 48,000 Hz before placing it on the response queue. Many providers originate at 24 kHz, but GPT-SoVITS and other configurable services can use different source rates; each worker owns the correct resampler.

The browser receives a JSON `audio_chunk` header followed by raw binary audio. `speech_id` is carried in the header, not inside the PCM frame.

The independent microphone-input queue is bounded at 300 messages and drops the oldest entry when full. That queue belongs to session input streaming and should not be confused with the TTS request/response queues.

## Error recovery

Workers report structured readiness, reconnecting, warning, and error messages through the response queue. The runtime classifies common failures such as rejected credentials, rate limits, quota, policy blocks, and connection failures.

Retryable failures are retried quietly at first; the frontend is notified after repeated failure. Non-retryable codes are reported immediately. A delayed respawn is guarded by the expected session and TTS mode so it cannot resurrect a worker for a session that has already changed.

## Voice creation and preview

Voice management is separate from streaming synthesis:

| Endpoint | Purpose |
|---|---|
| `POST /api/characters/voice_clone` | Multipart clone flow |
| `POST /api/characters/voice_clone_direct` | Direct/provider-specific clone flow |
| `POST /api/characters/voice_design_preview` | ElevenLabs voice-design preview |
| `POST /api/characters/voice_design_create` | Save a designed voice |
| `GET /api/characters/voices` | Effective voice catalog |
| `GET /api/characters/voice_preview` | Provider-aware preview |

The clone endpoint is not DashScope-only. It has explicit, symmetric branches for CosyVoice domestic/international, MiniMax domestic/international, ElevenLabs, MiMo, vLLM-Omni, and Doubao TTS. Some providers return a remote voice ID; others store reference audio locally and inline it on every synthesis request.

## Implementation map

| Concern | File |
|---|---|
| Path selection, queues, interruption, delivery | `main_logic/core/tts_runtime.py` |
| Provider dispatch and registration | `main_logic/tts_client/__init__.py` |
| Provider workers | `main_logic/tts_client/workers/` |
| Dispatch metadata | `utils/tts/provider_registry.py` |
| Native voice routing | `utils/tts/native_voice_registry.py` |
| Voice source storage | `utils/voice_config.py` |
| Clone and design endpoints | `main_routers/characters_router/voice_cloning.py` |
| Preview and voice catalog | `main_routers/characters_router/voice_preview.py` |
