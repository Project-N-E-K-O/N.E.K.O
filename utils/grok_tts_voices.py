"""xAI Grok TTS adapter: catalog metadata for grok streaming TTS voices.

Mirrors `utils.gemini_tts_voices` — cross-cutting decision logic lives in
`utils.native_voice_registry`; this module just wires Grok's 5 built-in voices
into the registry so `core._has_custom_tts()` correctly classifies them as
native (not custom), and `get_tts_worker` dispatches to
`grok_streaming_tts_worker` instead of falling through to `cosyvoice_vc_tts_worker`.

Voice list reference: xAI `GET /v1/tts/voices` (eve / ara / leo / rex / sal).
The upstream API expects lowercase voice ids; we mirror that in the catalog.
"""

from utils.native_voice_registry import (
    NativeVoiceProvider,
    register_provider,
)

GROK_TTS_DEFAULT_VOICE = "eve"
GROK_TTS_DEFAULT_MALE_VOICE = "leo"

# xAI's published voice catalog. Gender labels are best-effort inferences from
# the canonical given-name associations — xAI's docs only list voice_id + name
# + language, not gender. The labels feed the UI display only; routing /
# dispatch only consult the keys.
GROK_TTS_VOICE_GENDERS: dict[str, str] = {
    "eve": "Female",
    "ara": "Female",
    "leo": "Male",
    "rex": "Male",
    "sal": "Male",
}

_GROK_TTS_VOICE_ALIASES: dict[str, str] = {
    "male": GROK_TTS_DEFAULT_MALE_VOICE,
    "man": GROK_TTS_DEFAULT_MALE_VOICE,
    "男": GROK_TTS_DEFAULT_MALE_VOICE,
    "男声": GROK_TTS_DEFAULT_MALE_VOICE,
    "female": GROK_TTS_DEFAULT_VOICE,
    "woman": GROK_TTS_DEFAULT_VOICE,
    "女": GROK_TTS_DEFAULT_VOICE,
    "女声": GROK_TTS_DEFAULT_VOICE,
}

GROK_PROVIDER = NativeVoiceProvider(
    key="grok",
    catalog=GROK_TTS_VOICE_GENDERS,
    aliases=_GROK_TTS_VOICE_ALIASES,
    default_voice=GROK_TTS_DEFAULT_VOICE,
    default_male_voice=GROK_TTS_DEFAULT_MALE_VOICE,
    catalog_prefix="Grok",
)

register_provider(GROK_PROVIDER)


def normalize_grok_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Wire-format helper: map any user-input voice (canonical id, alias,
    or empty) to a canonical xAI voice id.

    Mirrors `utils.gemini_tts_voices.normalize_gemini_tts_voice`. The
    streaming TTS worker calls this before building the `voice` query
    parameter, because the routing layer accepts aliases like ``male`` /
    ``女声`` (via `NativeVoiceProvider.is_voice`) but xAI's endpoint only
    accepts canonical ids (eve/ara/leo/rex/sal) or 8-char custom voice ids.
    """
    return GROK_PROVIDER.normalize(voice_id)
