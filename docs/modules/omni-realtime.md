# Realtime Client

**Package:** `main_logic/omni_realtime_client/`

`OmniRealtimeClient` owns the native-audio conversation path selected by `LLMSessionManager` for audio input. The class is assembled from transport, response, audio, media/Gemini, and tool mixins behind the stable `main_logic.omni_realtime_client` import.

## Provider transports

| Provider route | Transport behavior |
|---|---|
| Qwen | DashScope realtime WebSocket events |
| OpenAI / GPT | OpenAI realtime WebSocket events; uplink audio is converted to 24 kHz |
| Step | Step realtime WebSocket events |
| GLM | Zhipu realtime WebSocket events |
| Grok | Realtime WebSocket event path |
| Gemini | Google GenAI SDK live session, not the raw WebSocket implementation |
| Free route | Endpoint-dependent: Gemini-proxy/live-stream or Step-compatible behavior |

The client selects one branch from `api_type`, model, and endpoint configuration. A connection failure is surfaced to the session manager; it does not silently create an `OmniOfflineClient`.

## Public lifecycle

| Method | Contract |
|---|---|
| `connect(instructions, ...)` | Open the selected provider session and configure turn detection, audio, tools, and instructions |
| `handle_messages()` | Run the receive loop for WebSocket transports |
| `update_session(config)` | Send provider-specific session updates |
| `stream_audio(audio_chunk)` | Process and upload one PCM input chunk |
| `stream_image(image_b64, bypass_rate_limit=False)` | Send or analyze one visual frame |
| `prime_context(text, skipped=False)` | Seed startup/hot-swap context using provider-specific semantics |
| `create_response(instructions, skipped=False)` | Add a user item and request a response |
| `inject_text_and_request_response(text, on_rejected=None)` | Perform the proactive text-injection/response operation atomically |
| `prompt_ephemeral(...)` | Request a temporary proactive response |
| `cancel_response()` / `handle_interruption()` | Cancel or truncate the active provider response where supported |
| `close()` | Stop background tasks, close the live transport, and release media/tool state |

Callbacks include streamed text and audio, input/output transcripts, response completion, tool calls, status, and connection errors. Interruption is handled through the lifecycle methods; there is no universal constructor-level `on_interrupt` event contract for this client.

## Audio and turn detection

The client accepts PCM bytes without a sample-rate argument and distinguishes the two capture formats used by the application:

- 480-sample / 960-byte PC chunks arrive at 48 kHz, pass through the RNNoise path, and are downsampled to the internal 16 kHz stream;
- 512-sample / 1024-byte mobile chunks are already 16 kHz and bypass PC denoising;
- OpenAI realtime upload is resampled from the internal stream to 24 kHz at the final send step.

`TurnDetectionMode.SERVER_VAD` is the default, but server VAD is not available on every route. Gemini, the free Gemini proxy, livestream, and explicit manual modes use client-side turn handling. The local fallback prefers RNNoise VAD when available and otherwise uses RMS-based speech detection with sustain/grace timing.

## Images

Qwen, GLM, GPT, Gemini, and the compatible free Gemini route can receive native visual frames. Other realtime models use the separately configured vision model to turn the first relevant frame into text context.

Native frames are throttled by `NATIVE_IMAGE_MIN_INTERVAL` (1.5 seconds). Idle capture multiplies the interval by `IMAGE_IDLE_RATE_MULTIPLIER` (5). `bypass_rate_limit=True` is reserved for a deliberate one-shot cue such as a proactive screenshot.

## Tools and proactive injection

Tool definitions are normalized once and encoded into the selected provider's supported wire format. Results are returned with provider-specific events. A bounded sliding-window guard prevents a realtime tool-call flood.

`inject_text_and_request_response()` is used by proactive callbacks that must speak immediately. It rejects or requeues work if another response owns the session rather than interleaving two response streams.

## Concurrency, backpressure, and failures

Audio and image processing have dedicated async locks. Sends are bounded by a semaphore, and fire-and-forget work is tracked so `close()` can cancel it. HTTP/WebSocket 503 responses trigger a short send throttle; fatal frame, timeout, and transport failures notify the connection-error callback and close the session.

These mechanisms protect one live provider connection. Provider failover and switching between text and audio clients remain session-manager responsibilities.
