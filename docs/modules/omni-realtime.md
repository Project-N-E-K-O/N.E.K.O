# Realtime Client

**File:** `main_logic/omni_realtime_client.py`

The `OmniRealtimeClient` manages the WebSocket connection to Realtime API providers (Qwen, OpenAI, Gemini, Step, GLM).

## Supported providers

| Provider | Protocol | Notes |
|----------|----------|-------|
| Qwen (DashScope) | WebSocket | Primary, most tested |
| OpenAI | WebSocket | GPT Realtime API |
| Step | WebSocket | Step Audio |
| GLM | WebSocket | Zhipu Realtime |
| Gemini | Google GenAI SDK | Uses SDK wrapper, not raw WebSocket |

## Key methods

### `connect()`

Establishes a WebSocket connection to the provider's Realtime API endpoint.

### `prime_context(text, skipped=False)`

Injects user text into the conversation as context. With `skipped=True` (or on Qwen) the text is appended to the session instructions without triggering a model response; with `skipped=False` (GPT/GLM/Step) it injects a one-shot user message and triggers a response.

### `create_response(instructions, skipped=False)`

Creates a user-role conversation message and triggers an LLM response. Used mid-conversation when an immediate model reply is needed.

### `inject_text_and_request_response(text, *, on_rejected=None)`

Injects a user-role text item and explicitly triggers a response in one call. Used by the voice-mode proactive path (agent task callbacks / plugin `push_message` `ai_behavior="respond"`) to speak a result immediately without waiting for the next user turn.

### `stream_audio(audio_chunk)`

Streams a raw PCM audio chunk to the LLM. The input sample rate is auto-detected from the chunk size (480 samples = 48 kHz from PC, which is RNNoise-denoised and downsampled to 16 kHz; 512 samples = 16 kHz from mobile, passed through directly), so no sample-rate argument is needed.

### `stream_image(image_b64, *, bypass_rate_limit=False)`

Streams a screenshot / camera frame for multi-modal understanding. Rate-limited by `NATIVE_IMAGE_MIN_INTERVAL` (1.5s default); pass `bypass_rate_limit=True` to skip the throttle for a single deliberate cue image (e.g. a proactive callback's screenshot).

## Event handlers

| Event | Purpose |
|-------|---------|
| `on_text_delta()` | Streamed text response from the LLM |
| `on_audio_delta()` | Streamed audio response |
| `on_input_transcript()` | User's speech converted to text (STT) |
| `on_output_transcript()` | LLM's output as text |
| `on_interrupt()` | User interrupted the LLM's output |

## Turn detection

The client uses **server-side VAD** (Voice Activity Detection) by default. The LLM provider decides when the user has finished speaking, enabling natural conversation turn-taking.

## Image throttling

Screen captures are rate-limited to avoid overwhelming the API:

- **Active speaking**: Images sent every `NATIVE_IMAGE_MIN_INTERVAL` seconds (1.5s)
- **Idle (no voice)**: Interval multiplied by `IMAGE_IDLE_RATE_MULTIPLIER` (5x = 7.5s)
