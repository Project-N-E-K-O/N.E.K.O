"""Gemini TTS adapter: catalog metadata + thin wrappers for wire-format paths.

The cross-cutting decision logic (catalog membership, routing, UI catalog,
realtime active-provider lookup, worker dispatch) lives in
`utils.native_voice_registry`. This module just wires Gemini into that
registry and keeps a couple of short aliases for code that's already
Gemini-bound by virtue of speaking Gemini's wire format (the
`gemini_tts_worker` HTTP call and the Gemini Live `speech_config` setup).

Voice list reference: https://ai.google.dev/gemini-api/docs/speech-generation
"""

from utils.native_voice_registry import (
    NativeVoiceProvider,
    register_provider,
)

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_DEFAULT_VOICE = "Leda"
GEMINI_TTS_DEFAULT_MALE_VOICE = "Puck"

GEMINI_TTS_VOICE_GENDERS: dict[str, str] = {
    "Achernar": "Female",
    "Achird": "Male",
    "Algenib": "Male",
    "Algieba": "Male",
    "Alnilam": "Male",
    "Aoede": "Female",
    "Autonoe": "Female",
    "Callirrhoe": "Female",
    "Charon": "Male",
    "Despina": "Female",
    "Enceladus": "Male",
    "Erinome": "Female",
    "Fenrir": "Male",
    "Gacrux": "Female",
    "Iapetus": "Male",
    "Kore": "Female",
    "Laomedeia": "Female",
    "Leda": "Female",
    "Orus": "Male",
    "Pulcherrima": "Female",
    "Puck": "Male",
    "Rasalgethi": "Male",
    "Sadachbia": "Male",
    "Sadaltager": "Male",
    "Schedar": "Male",
    "Sulafat": "Female",
    "Umbriel": "Male",
    "Vindemiatrix": "Female",
    "Zephyr": "Female",
    "Zubenelgenubi": "Male",
}

_GEMINI_TTS_VOICE_ALIASES: dict[str, str] = {
    "male": GEMINI_TTS_DEFAULT_MALE_VOICE,
    "man": GEMINI_TTS_DEFAULT_MALE_VOICE,
    "masculine": GEMINI_TTS_DEFAULT_MALE_VOICE,
    "男": GEMINI_TTS_DEFAULT_MALE_VOICE,
    "男声": GEMINI_TTS_DEFAULT_MALE_VOICE,
    "中文男": GEMINI_TTS_DEFAULT_MALE_VOICE,
    "female": GEMINI_TTS_DEFAULT_VOICE,
    "woman": GEMINI_TTS_DEFAULT_VOICE,
    "feminine": GEMINI_TTS_DEFAULT_VOICE,
    "女": GEMINI_TTS_DEFAULT_VOICE,
    "女声": GEMINI_TTS_DEFAULT_VOICE,
    "中文女": GEMINI_TTS_DEFAULT_VOICE,
}

GEMINI_PROVIDER = NativeVoiceProvider(
    key="gemini",
    catalog=GEMINI_TTS_VOICE_GENDERS,
    aliases=_GEMINI_TTS_VOICE_ALIASES,
    default_voice=GEMINI_TTS_DEFAULT_VOICE,
    default_male_voice=GEMINI_TTS_DEFAULT_MALE_VOICE,
    catalog_prefix="Gemini",
)

register_provider(GEMINI_PROVIDER)


def normalize_gemini_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Wire-format helper for Gemini-bound code paths (gemini_tts_worker,
    omni_realtime_client). Cross-cutting code should go through the registry."""
    return GEMINI_PROVIDER.normalize(voice_id)


def is_gemini_tts_voice(voice_id: str | None) -> bool:
    return GEMINI_PROVIDER.is_voice(voice_id)
