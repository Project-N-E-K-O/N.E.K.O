# TTS Client

**Package:** `main_logic/tts_client/`

The TTS package resolves the external speech provider selected by the current core and voice configuration. It supplies worker functions with one queue contract; `LLMSessionManager` owns the queues, starts the daemon thread, and forwards produced PCM to the client.

Native-audio realtime models do not use this external TTS path.

## Factory contract

```python
from main_logic.tts_client import get_tts_worker

worker_fn, api_key_override, provider_key = get_tts_worker(
    core_api_type="qwen",
    has_custom_voice=False,
    voice_id="",
)
```

`get_tts_worker()` does not create or start a worker object. It returns:

1. the worker function to run in the session manager's thread;
2. an optional API-key override required by that route;
3. the canonical `provider_key` used for key resolution and diagnostics.

Unsupported or unusable selections resolve to the dummy worker so the queue reports a controlled error instead of pretending synthesis succeeded.

## Provider selection

Registered special routes are evaluated in priority order before native/core-provider fallback:

1. GPT-SoVITS
2. vLLM-Omni
3. MiniMax cloned voice
4. ElevenLabs cloned voice
5. CosyVoice cloned voice
6. MiMo
7. Doubao TTS

The remaining selection follows the active core route, including Qwen/Qwen International, free-route Step or Gemini behavior, Step, GLM/CogTTS, Gemini, OpenAI, and Grok. Voice metadata and the selected provider decide which clone route is valid; cloning is not DashScope-only.

Provider implementations live under `main_logic/tts_client/workers/`. They normalize their output to the PCM format expected by the application, including provider-specific decode and resampling to 48 kHz where required.

## Queue protocol

The manager sends request items to the worker thread:

| Request | Meaning |
|---|---|
| `(speech_id, text)` | Synthesize one text segment |
| `(None, None)` | End the current utterance |
| `("__interrupt__", None)` | Discard/mute the current utterance and reset worker state |
| `("__shutdown__", None)` | Stop the worker |

Workers return raw PCM chunks or tagged `("__audio__", speech_id, payload)` messages, plus control messages such as ready and error states. The `speech_id` lets the manager reject late audio from an interrupted or superseded response.

## Thread and async boundary

Model text is produced on the asyncio side. The session manager segments it and enqueues TTS requests; the selected synchronous worker consumes them in its dedicated thread. A manager response task reads the response queue without blocking the event loop and forwards accepted audio to WebSocket clients.

The TTS package therefore does not own session lifecycle, WebSocket delivery, or the worker thread itself.

## Interruption and errors

On interruption, the manager drains queued output, sends the interrupt sentinel, waits for worker acknowledgement where supported, drains late chunks, and clears pending speech IDs. Provider workers differ: some can cancel an upstream request, while others can only stop emitting or mute its eventual result. The contract does not promise that every remote API call is physically cancelled.

Startup and synthesis failures are returned through control/error messages. The manager decides whether to retry, surface status, restart the worker, or disable that speech path. Voice-enrollment HTTP routes and persistence of cloned `voice_id` values are separate from the runtime TTS queue.

### GPT-SoVITS utterance lifecycle

The worker has one request-queue consumer. Each utterance owns an asyncio task for connecting, sending text, receiving audio, and draining completion. `(None, None)` queues that utterance's end operation. A normal successor waits for the previous utterance's tail and completion in its own task, while the control loop keeps reading requests throughout the up-to-30-second drain. A new speech ID replaces an utterance whose end has not been queued. `__interrupt__` cancels active and queued utterances, muting their output before closing their sockets. Connection, handshake, retry, and send waits are cancellable, and old cleanup cannot alter a replacement connection.

PCM is always tagged as `("__audio__", original_speech_id, payload)`. Cancelled utterances suppress late audio, errors, and completion. Normal completion flushes the resampler tail before one `__audio_done__` for that ID; interruption discards the old tail and omits normal completion. Shutdown waits for receiver/socket cleanup. The initial readiness probe has no speech ID and emits no audio completion.

These guarantees cover client-side connection cancellation and stale-output suppression. Physical inference cancellation still depends on Bridge and the host. `tests/unit/test_gptsovits_worker_lifecycle.py` drives the real worker with controlled sockets and event barriers, without local voices or running services.
