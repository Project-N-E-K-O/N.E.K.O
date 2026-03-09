# Offline Client

**File:** `main_logic/omni_offline_client.py`

The `OmniOfflineClient` provides text-based LLM conversation as a fallback when the Realtime API is unavailable.

## When it's used

- When the selected provider doesn't support Realtime API
- When using local LLM deployments (Ollama, etc.)
- When voice input is disabled and text-only mode is preferred

## Capabilities

- Text-in, text-out conversation
- Compatible with any OpenAI-compatible API endpoint
- Uses LangChain (`langchain_openai.ChatOpenAI`) for LLM integration
- Supports conversation history and system prompts
- Independent vision model configuration (`vision_model`, `vision_base_url`, `vision_api_key`)
- Proactive message generation via `stream_proactive(instruction)`
- Repetition detection and response discard handling

## Key methods

| Method | Purpose |
|--------|---------|
| `connect()` | Initialize the chat model |
| `stream_audio(data)` | Accept audio input (requires separate STT) |
| `stream_image(b64)` | Accept image input for vision model |
| `create_response()` | Generate an LLM response |
| `stream_proactive(instruction)` | Generate a proactive (character-initiated) message |
| `switch_model(new_model, use_vision_config)` | Hot-switch to a different model |
| `has_pending_images()` | Check if there are unprocessed images |

## Differences from Realtime Client

| Feature | Realtime Client | Offline Client |
|---------|----------------|----------------|
| Audio I/O | Native | Requires separate STT/TTS |
| Streaming | WebSocket bidirectional | HTTP streaming |
| Multi-modal | Native (audio + images) | Vision model (separate config) |
| Latency | Lower (persistent connection) | Higher (per-request) |
| Provider support | Limited (Realtime API required) | Any OpenAI-compatible |
| Proactive messages | `stream_proactive()` | `stream_proactive()` |
