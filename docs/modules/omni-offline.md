# Offline Client

**Package:** `main_logic/omni_offline_client/`

`OmniOfflineClient` is the streamed chat-completion client used by text-input sessions. `LLMSessionManager` selects it explicitly when `input_mode == "text"`; it is not an automatic fallback after a realtime connection fails.

The public class is composed from lifecycle, streaming, media, tool, and Gemini-support mixins. `main_logic.omni_offline_client` remains the stable import path.

## Request model

`connect(instructions)` initializes the in-memory system message and client state. It does not open a persistent model socket. Each `stream_text()` call performs an async streamed request, emits callbacks, and then updates conversation history.

The main lifecycle methods are:

| Method | Contract |
|---|---|
| `connect(instructions, native_audio=False)` | Initialize the system prompt and history |
| `stream_text(text)` | Send a user turn, stream visible text, and persist the completed turn |
| `stream_image(image_b64)` | Stage an image for the next text turn |
| `switch_model(model, use_vision_config=False)` | Replace the chat client under an async model-switch lock |
| `prime_context(text, skipped=False)` | Append startup/hot-swap context to the system message |
| `create_response(instructions, skipped=False)` | Append a persistent user message; retained as the realtime-compatible interface |
| `prompt_ephemeral(...)` | Run a temporary instruction without persisting that instruction |
| `cancel_response()` / `handle_interruption()` | Stop accepting chunks from the active response |
| `close()` | Close HTTP/SDK clients and clear history and staged media |

`stream_audio()` and `send_event()` are compatibility no-ops. Text mode does not perform STT inside this client.

## Providers and multimodal input

The normal path uses the project's chat-LLM adapter for configured OpenAI- and Anthropic-compatible providers. Eligible native Gemini configurations use the Google GenAI SDK path instead. Provider compatibility depends on the adapter and configured endpoint; it is not guaranteed for every arbitrary OpenAI-shaped server.

The client is not text-only. `stream_image()` queues base64 images, and the next `stream_text()` builds a multimodal user message. A separately configured vision model and endpoint can be selected when images are present. Old image payloads are evicted from retained history to limit growth.

External speech synthesis is owned by `LLMSessionManager` and the [TTS client](/modules/tts-client), not by `OmniOfflineClient`.

## Tool calls

`set_tools()`, `set_tool_call_handler()`, and `has_tools()` manage the current tool contract. Tool-aware streaming supports the compatible chat formats and the native Gemini SDK format, validates tool calls, invokes the manager callback, and feeds results back to the model. `max_tool_iterations` bounds repeated model/tool cycles and defaults to three.

## Async and cancellation boundaries

- Streaming and lifecycle methods are async; model replacement is serialized by `_model_switch_lock`.
- Conversation history belongs to one client instance. Parallel user turns are not a supported way to mutate it.
- `cancel_response()` changes the response state so later chunks are discarded. It does not promise transport-level cancellation of every provider request already in flight.
- `close()` also offloads the synchronous Gemini SDK close operation so it does not block the event loop.

## Retry and failure behavior

Normal text streaming retries retryable model/network errors up to three total attempts with short backoff. A close or interruption stops further attempts. Account, API-key, quota, safety, empty-completion, repetition, and response-length conditions have separate status/discard callbacks so the manager can surface or recover from them.

If construction, model switching, or all retries fail, the error is reported through callbacks or raised to the session layer. The client does not switch itself to `OmniRealtimeClient` or to another provider.
