# TTS Client

**File:** `main_logic/tts_client.py`

The TTS client handles text-to-speech synthesis across multiple providers with a unified queue-based interface.

## Factory function

```python
from main_logic.tts_client import get_tts_worker

worker = get_tts_worker(core_api_type='qwen', has_custom_voice=False)
```

Creates a TTS worker configured for the active provider and voice settings.

## Provider resolution

The factory follows this priority:

1. If `tts_custom` is configured: `gptsovits_tts_worker` (HTTP) or `local_cosyvoice_worker` (WebSocket)
2. If `has_custom_voice=True`: `cosyvoice_vc_tts_worker` (DashScope voice cloning)
3. By `core_api_type`: route to the matching worker
4. Fallback: `dummy_tts_worker`

## Supported providers

| Provider | Worker function | Protocol | Features |
|----------|----------------|----------|----------|
| Qwen CosyVoice | `qwen_realtime_tts_worker` | WebSocket | DashScope Realtime TTS |
| CosyVoice VC | `cosyvoice_vc_tts_worker` | DashScope SDK | Custom voice (cloned) |
| StepFun | `step_realtime_tts_worker` | WebSocket | step-tts-2, free mode via `wss://lanlan.tech/tts` |
| GLM CogTTS | `cogtts_tts_worker` | WebSocket | Zhipu CogTTS |
| Gemini | `gemini_tts_worker` | Google GenAI SDK | Google Gemini TTS |
| OpenAI | `openai_tts_worker` | HTTP | OpenAI TTS API |
| GPT-SoVITS v3 | `gptsovits_tts_worker` | HTTP→WebSocket | Local custom TTS |
| Local CosyVoice | `local_cosyvoice_worker` | WebSocket | Direct local CosyVoice server |
| Free | `step_realtime_tts_worker(free_mode=True)` | WebSocket | Free tier via lanlan.tech |
| (none) | `dummy_tts_worker` | — | No-op fallback |

## Queue architecture

The TTS client uses a producer-consumer pattern:

1. **Request queue**: Text sentences enqueued by the session manager
2. **Worker thread**: Dequeues text, calls the TTS API, produces audio chunks
3. **Response queue**: Audio chunks ready for resampling and WebSocket delivery

## Voice cloning flow

1. User uploads audio sample via the characters API
2. Audio is sent to DashScope's voice enrollment API
3. A `voice_id` is returned and stored in character config
4. Subsequent TTS calls use `cosyvoice_vc_tts_worker` with the `voice_id`
5. The system tries local TTS first before falling back to cloud

## Interruption

When the user interrupts:

1. Both queues are flushed
2. Any in-progress TTS API call is cancelled
3. The worker is immediately ready for new input
