# TTS Client

**File:** `main_logic/tts_client.py` (~2300 lines)

The TTS client handles text-to-speech synthesis across multiple providers with a unified queue-based interface.

## Factory function

```python
from main_logic.tts_client import get_tts_worker

worker = get_tts_worker(config)
```

Creates a TTS worker configured for the active provider and voice settings.

## Supported providers

| Provider | Module | Features |
|----------|--------|----------|
| DashScope CosyVoice | Cloud | High quality, voice cloning, streaming |
| DashScope TTS V2 | Cloud | Lower latency variant |
| GPT-SoVITS | Local | Fully offline, customizable |
| Custom | HTTP | Any OpenAI-compatible TTS endpoint |

## Queue architecture

The TTS client uses a producer-consumer pattern:

1. **Request queue**: Text sentences enqueued by the session manager
2. **Worker thread**: Dequeues text, calls the TTS API, produces audio chunks
3. **Response queue**: Audio chunks ready for resampling and WebSocket delivery

## Voice cloning flow

1. User uploads audio sample via `/api/characters/voice_clone`
2. Audio is sent to DashScope's voice enrollment API
3. A `voice_id` is returned and stored in character config
4. Subsequent TTS calls include the `voice_id` for personalized synthesis

## Interruption

When the user interrupts:

1. Both queues are flushed
2. Any in-progress TTS API call is cancelled
3. The worker is immediately ready for new input
