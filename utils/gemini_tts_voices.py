"""Gemini TTS adapter: catalog metadata + thin wrappers for wire-format paths.

The cross-cutting decision logic (catalog membership, routing, UI catalog,
realtime active-provider lookup, worker dispatch) lives in
`utils.native_voice_registry`. This module just wires Gemini into that
registry and keeps a couple of short aliases for code that's already
Gemini-bound by virtue of speaking Gemini's wire format (the
`gemini_tts_worker` HTTP call and the Gemini Live `speech_config` setup).

音色 ID、展示性别和默认值读取自 config/api_providers.json 的
native_tts_voice_providers.gemini，避免再次硬编码 voice 列表。
fallback 常量只在配置整体加载失败时兜底，正常分发都来自 JSON。

Voice list reference: https://ai.google.dev/gemini-api/docs/speech-generation
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.native_voice_registry import (
    NativeVoiceProvider,
    register_provider,
)

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"

FALLBACK_GEMINI_TTS_DEFAULT_VOICE = "Leda"
FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE = "Puck"


def _load_provider_config() -> dict:
    return get_native_tts_voice_provider_config("gemini")


_CFG = _load_provider_config()

# Public catalog mirror — kept for callers (and tests) that still read voice
# metadata directly. Empty when the JSON config is missing, which means
# Gemini TTS is effectively unavailable; we don't pre-fill from code so the
# failure surfaces instead of silently using stale data.
GEMINI_TTS_VOICE_GENDERS: dict[str, str] = _CFG.get("voices") or {}
GEMINI_TTS_DEFAULT_VOICE = _CFG.get("default_voice") or FALLBACK_GEMINI_TTS_DEFAULT_VOICE
GEMINI_TTS_DEFAULT_MALE_VOICE = (
    _CFG.get("default_male_voice")
    or FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE
    or GEMINI_TTS_DEFAULT_VOICE
)


def _build_aliases(configured: dict[str, str]) -> dict[str, str]:
    """Casefold alias keys so NativeVoiceProvider.normalize 的 casefold 查表能命中。
    与 stepfun_tts_voices._build_aliases 的差别：Gemini 的 catalog value 是性别
    (Female/Male) 而非展示名，不应把它当 alias 注入回去。"""
    return {
        alias.casefold(): voice_id
        for alias, voice_id in configured.items()
        if alias and voice_id
    }


def _create_provider() -> NativeVoiceProvider | None:
    if not GEMINI_TTS_VOICE_GENDERS or not GEMINI_TTS_DEFAULT_VOICE:
        return None
    return NativeVoiceProvider(
        key="gemini",
        catalog=GEMINI_TTS_VOICE_GENDERS,
        aliases=_build_aliases(_CFG.get("aliases") or {}),
        default_voice=GEMINI_TTS_DEFAULT_VOICE,
        default_male_voice=GEMINI_TTS_DEFAULT_MALE_VOICE,
        catalog_prefix=_CFG.get("catalog_prefix") or "Gemini",
        catalog_value_is_display_name=bool(
            _CFG.get("catalog_value_is_display_name", False)
        ),
    )


GEMINI_PROVIDER = _create_provider()

if GEMINI_PROVIDER is not None:
    register_provider(GEMINI_PROVIDER)


def normalize_gemini_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Wire-format helper for Gemini-bound code paths (gemini_tts_worker,
    omni_realtime_client). Cross-cutting code should go through the registry.

    Empty / unrecognized input always resolves to ``GEMINI_TTS_DEFAULT_VOICE``
    (Leda by default) so callers like gemini_tts_worker never send an empty
    ``voiceName`` to the Gemini API — even on the degraded code path where
    ``api_providers.json`` failed to load and `GEMINI_PROVIDER` is None.
    Matches the behavior NativeVoiceProvider.normalize() provides in the
    happy path.
    """
    if GEMINI_PROVIDER is None:
        normalized = (voice_id or "").strip()
        return (normalized or GEMINI_TTS_DEFAULT_VOICE), False
    return GEMINI_PROVIDER.normalize(voice_id)


def is_gemini_tts_voice(voice_id: str | None) -> bool:
    if GEMINI_PROVIDER is None:
        return False
    return GEMINI_PROVIDER.is_voice(voice_id)
