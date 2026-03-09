# Realtime Client

**File:** `main_logic/omni_realtime_client.py`

The `OmniRealtimeClient` manages the WebSocket connection to Realtime API providers (Qwen, OpenAI, Gemini, Step, GLM).

## Supported providers

| Provider | Protocol | Notes |
|----------|----------|-------|
| Qwen (DashScope) | WebSocket | Primary, Momo voice, gummy-realtime-v1 transcription |
| OpenAI | WebSocket | GPT Realtime API (gpt-realtime-mini, semantic VAD, marin voice) |
| Step | WebSocket | Step Audio (qingchunshaonv voice, web_search tool) |
| GLM | WebSocket | Zhipu Realtime (video_passive, tongtong voice) |
| Gemini | Google GenAI SDK | Uses SDK wrapper (`_connect_gemini`), not raw WebSocket |
| Free | WebSocket | Same as Step configuration without tools |

## Key methods

### `connect()`

Establishes a WebSocket connection to the provider's Realtime API endpoint. Gemini uses a dedicated `_connect_gemini()` path via the Google GenAI SDK.

### `send_text(text)`

Sends user text input to the LLM.

### `send_audio(audio_bytes, sample_rate)`

Streams user audio chunks to the LLM. Audio is sent as raw PCM data.

### `send_screenshot(base64_data)`

Sends a screenshot for multi-modal understanding. Rate-limited by `NATIVE_IMAGE_MIN_INTERVAL` (1.5s default).

### `stream_proactive(instruction)`

Initiates a proactive (character-initiated) message stream with the given instruction.

## Event handlers

| Event | Purpose |
|-------|---------|
| `on_text_delta()` | Streamed text response from the LLM |
| `on_audio_delta()` | Streamed audio response |
| `on_input_transcript()` | User's speech converted to text (STT) |
| `on_output_transcript()` | LLM's output as text |
| `on_interrupt()` | User interrupted the LLM's output |
| `on_response_done()` | Full response finished |
| `on_repetition_detected()` | Repetition in output detected |
| `on_response_discarded()` | Response discarded (repetition, error) |

## Turn detection

The client uses **server-side VAD** (Voice Activity Detection) by default. The LLM provider decides when the user has finished speaking, enabling natural conversation turn-taking.

## Image throttling

Screen captures are rate-limited to avoid overwhelming the API:

- **Active speaking**: Images sent every `NATIVE_IMAGE_MIN_INTERVAL` seconds (1.5s)
- **Idle (no voice)**: Interval multiplied by `IMAGE_IDLE_RATE_MULTIPLIER` (5x = 7.5s)

## Vision analysis

The `_analyze_image_with_vision_model(image_b64)` method provides standalone vision model analysis for screenshots, used alongside the main LLM conversation.
