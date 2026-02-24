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

### `send_text(text)`

Sends user text input to the LLM.

### `send_audio(audio_bytes, sample_rate)`

Streams user audio chunks to the LLM. Audio is sent as raw PCM data.

### `send_screenshot(base64_data)`

Sends a screenshot for multi-modal understanding. Rate-limited by `NATIVE_IMAGE_MIN_INTERVAL` (1.5s default).

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
