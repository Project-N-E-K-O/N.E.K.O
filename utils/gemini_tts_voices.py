"""Gemini TTS voice catalog shared by validation, UI, and synthesis.

Voice list reference: https://ai.google.dev/gemini-api/docs/speech-generation
"""

from collections.abc import Callable

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

_GEMINI_TTS_VOICE_LOOKUP = {
    voice_name.casefold(): voice_name for voice_name in GEMINI_TTS_VOICE_GENDERS
}

_GEMINI_TTS_VOICE_ALIASES = {
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


def normalize_gemini_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Return a supported Gemini voice and whether the input was recognized.

    Empty / whitespace input is treated as unrecognized so callers can tell
    "user explicitly chose a Gemini voice" apart from "we picked the default".
    """
    normalized = (voice_id or "").strip()
    if not normalized:
        return GEMINI_TTS_DEFAULT_VOICE, False

    exact_match = _GEMINI_TTS_VOICE_LOOKUP.get(normalized.casefold())
    if exact_match:
        return exact_match, True

    alias_match = _GEMINI_TTS_VOICE_ALIASES.get(normalized.casefold())
    if alias_match:
        return alias_match, True

    return GEMINI_TTS_DEFAULT_VOICE, False


def is_gemini_tts_voice(voice_id: str | None) -> bool:
    """Return True when the voice is a Gemini TTS voice or supported alias."""
    return normalize_gemini_tts_voice(voice_id)[1]


def resolve_gemini_native_voice_for_routing(
    core_api_type: str | None,
    voice_id: str | None,
    voice_id_exists: Callable[[str], bool] | None = None,
) -> tuple[str, bool]:
    """Return the Gemini Live voice and whether it should use native routing.

    A user-cloned voice with the same raw or canonical voice id wins over the
    Gemini built-in voice, so the caller can keep custom TTS routing intact.
    """
    normalized_voice, recognized = normalize_gemini_tts_voice(voice_id)
    if core_api_type != "gemini" or not recognized:
        return normalized_voice, False

    if voice_id_exists is None:
        return normalized_voice, True

    candidates = {voice_id, normalized_voice}
    has_custom_collision = any(
        voice_id_exists(candidate)
        for candidate in candidates
        if candidate
    )
    return normalized_voice, not has_custom_collision


def get_gemini_tts_voices() -> dict[str, dict[str, str | bool]]:
    """Return Gemini voices in the shape expected by the character UI."""
    return {
        voice_name: {
            "prefix": f"Gemini {voice_name} ({gender})",
            "provider": "gemini",
            "gender": gender,
            "builtin": True,
        }
        for voice_name, gender in GEMINI_TTS_VOICE_GENDERS.items()
    }
