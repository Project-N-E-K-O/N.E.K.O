"""ElevenLabs TTS voice metadata shared by router and worker code."""

ELEVENLABS_VOICE_PREFIX = "eleven:"
ELEVENLABS_DEFAULT_BASE_URL = "https://api.elevenlabs.io"
ELEVENLABS_DEFAULT_MODEL = "eleven_flash_v2_5"
ELEVENLABS_DEFAULT_OUTPUT_FORMAT = "pcm_24000"
ELEVENLABS_DEFAULT_OPTIMIZE_STREAMING_LATENCY = 0


def is_elevenlabs_voice_id(voice_id: str | None) -> bool:
    return (voice_id or "").strip().startswith(ELEVENLABS_VOICE_PREFIX)


def normalize_elevenlabs_voice_id(voice_id: str | None) -> str:
    raw = (voice_id or "").strip()
    if raw.startswith(ELEVENLABS_VOICE_PREFIX):
        return raw[len(ELEVENLABS_VOICE_PREFIX):].strip()
    return raw
