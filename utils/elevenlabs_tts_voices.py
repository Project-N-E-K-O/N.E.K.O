"""ElevenLabs TTS adapter: wire-format constants + thin voice helpers.

Unlike Gemini/Grok, ElevenLabs voices are user/account scoped rather than a
fixed native catalog, so this module does not register a NativeVoiceProvider.
It still mirrors the small adapter shape used by the native TTS helpers:
provider-specific constants live here, and ElevenLabs-bound code calls the
normalizer before sending the raw voice id to the upstream API.
"""

ELEVENLABS_TTS_VOICE_PREFIX = "eleven:"
ELEVENLABS_TTS_DEFAULT_MODEL = "eleven_flash_v2_5"
ELEVENLABS_TTS_DEFAULT_OUTPUT_FORMAT = "pcm_24000"
ELEVENLABS_TTS_DEFAULT_OPTIMIZE_STREAMING_LATENCY = 0

# Backward-compatible names for older call sites.
ELEVENLABS_VOICE_PREFIX = ELEVENLABS_TTS_VOICE_PREFIX
ELEVENLABS_DEFAULT_MODEL = ELEVENLABS_TTS_DEFAULT_MODEL
ELEVENLABS_DEFAULT_OUTPUT_FORMAT = ELEVENLABS_TTS_DEFAULT_OUTPUT_FORMAT
ELEVENLABS_DEFAULT_OPTIMIZE_STREAMING_LATENCY = ELEVENLABS_TTS_DEFAULT_OPTIMIZE_STREAMING_LATENCY


def normalize_elevenlabs_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Return the upstream ElevenLabs voice id and whether the prefix matched."""
    raw = (voice_id or "").strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw[len(ELEVENLABS_TTS_VOICE_PREFIX):].strip(), True
    return raw, False


def is_elevenlabs_tts_voice(voice_id: str | None) -> bool:
    return normalize_elevenlabs_tts_voice(voice_id)[1]


def normalize_elevenlabs_voice_id(voice_id: str | None) -> str:
    return normalize_elevenlabs_tts_voice(voice_id)[0]


def is_elevenlabs_voice_id(voice_id: str | None) -> bool:
    return is_elevenlabs_tts_voice(voice_id)
