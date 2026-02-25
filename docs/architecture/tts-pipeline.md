# TTS Pipeline

N.E.K.O. supports multiple TTS (Text-to-Speech) providers with a unified queue-based architecture that enables streaming audio output and real-time interruption.

## Architecture

```
LLM text output
      │
      ▼
TTS Request Queue ──> TTS Worker Thread
                           │
                      ┌────┼────────────┐
                      │    │            │
                      ▼    ▼            ▼
                  CosyVoice  GPT-SoVITS  Custom
                  (DashScope) (Local)     Endpoint
                      │    │            │
                      └────┼────────────┘
                           │
                      TTS Response Queue
                           │
                      Audio Resampler (24→48 kHz)
                           │
                      WebSocket ──> Browser
```

## Supported providers

| Provider | Type | Features |
|----------|------|----------|
| **DashScope CosyVoice** | Cloud API | High quality, voice cloning, multiple voice tones |
| **DashScope TTS V2** | Cloud API | Faster, lower latency |
| **GPT-SoVITS** | Local service | Fully offline, customizable |
| **Custom endpoint** | User-defined | Any OpenAI-compatible TTS API |

## Queue-based streaming

The TTS pipeline uses a producer-consumer pattern:

1. **Producer** (main thread): As the LLM streams text output, complete sentences are enqueued to `tts_request_queue`.
2. **Consumer** (TTS worker thread): Dequeues text, synthesizes audio, enqueues PCM chunks to `tts_response_queue`.
3. **Sender** (main thread): Dequeues audio chunks, resamples from 24kHz to 48kHz, and sends via WebSocket.

## Interruption handling

When the user starts speaking while the character is still talking:

1. The LLM provider fires an `on_interrupt` event
2. Both TTS queues are flushed immediately
3. Pending audio is discarded
4. The system is ready for the new user input

## Voice cloning

Users can create custom voices by uploading a ~15-second clean audio sample:

1. Upload audio via `/api/characters/voice_clone` (multipart form)
2. The audio is sent to DashScope's voice cloning API
3. A unique `voice_id` is returned and stored in the character config
4. All subsequent TTS requests for that character use the cloned voice

## Audio format

| Parameter | Value |
|-----------|-------|
| LLM output sample rate | 24,000 Hz |
| Browser playback rate | 48,000 Hz |
| Format | PCM 16-bit signed little-endian |
| Channels | Mono |
| Resampler | soxr (high quality) |

## Free voices

N.E.K.O. includes built-in voice tones that don't require a custom API key:

| Name (Chinese) | Voice ID |
|----------------|----------|
| 俏皮女孩 (Playful Girl) | `voice-tone-OdVwaw2Az2` |
| 可爱女孩 (Cute Girl) | `voice-tone-OdVwrbG3No` |
| 可爱少女 (Cute Maiden) | `voice-tone-OdVx7X482K` |
| 温柔少女 (Gentle Maiden) | `voice-tone-OdVyxjm0lk` |
| 清冷御姐 (Cool Elder Sister) | `voice-tone-OdVyPmim9I` |
| And 5 more... | |
