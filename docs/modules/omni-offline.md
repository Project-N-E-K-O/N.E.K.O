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
- Uses LangChain for LLM integration
- Supports conversation history and system prompts

## Differences from Realtime Client

| Feature | Realtime Client | Offline Client |
|---------|----------------|----------------|
| Audio I/O | Native | Requires separate STT/TTS |
| Streaming | WebSocket bidirectional | HTTP streaming |
| Multi-modal | Native (audio + images) | Text only |
| Latency | Lower (persistent connection) | Higher (per-request) |
| Provider support | Limited (Realtime API required) | Any OpenAI-compatible |
