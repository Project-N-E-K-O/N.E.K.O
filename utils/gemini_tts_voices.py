"""Gemini TTS adapter: catalog metadata + thin wrappers for wire-format paths.

The cross-cutting decision logic (catalog membership, routing, UI catalog,
realtime active-provider lookup, worker dispatch) lives in
`utils.native_voice_registry`. This module just wires Gemini into that
registry and keeps a couple of short aliases for code that's already
Gemini-bound by virtue of speaking Gemini's wire format (the
`gemini_tts_worker` HTTP call and the Gemini Live `speech_config` setup).

音色 ID、展示性别和默认值优先读取自 config/api_providers.json 的
native_tts_voice_providers.gemini，避免修改音色清单要动 Python 代码。
fallback 常量是 PR #1290 之前的硬编码目录的副本，仅在 JSON 加载失败时兜底
—— 此时 provider 仍必须留在 registry 里，否则
`resolve_native_voice_for_routing("gemini", ...)` 会判 native=False，
`core._has_custom_tts()` 把内置音色当 custom，最终把 Puck/Leda 也路由到
cosyvoice_vc_tts_worker，比"丢失目录元数据"更隐蔽的 routing 回归。

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

# 与 api_providers.json 的 native_tts_voice_providers.gemini.voices 保持
# 同形；config 是权威源，这份是 JSON 加载失败时的兜底，保证 provider 始终
# 注册成功、routing 不退化到 cosyvoice。两边漂移的代价仅仅是"新版 JSON
# 加的音色在 config 缺失时不可见"，比 routing 走错路要轻。
_FALLBACK_GEMINI_TTS_VOICE_GENDERS: dict[str, str] = {
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

_FALLBACK_GEMINI_TTS_VOICE_ALIASES: dict[str, str] = {
    "male": FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE,
    "man": FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE,
    "masculine": FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE,
    "男": FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE,
    "男声": FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE,
    "中文男": FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE,
    "female": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
    "woman": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
    "feminine": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
    "女": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
    "女声": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
    "中文女": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
}


def _load_provider_config() -> dict:
    return get_native_tts_voice_provider_config("gemini")


_CFG = _load_provider_config()

GEMINI_TTS_VOICE_GENDERS: dict[str, str] = (
    _CFG.get("voices") or _FALLBACK_GEMINI_TTS_VOICE_GENDERS
)
GEMINI_TTS_DEFAULT_VOICE = (
    _CFG.get("default_voice") or FALLBACK_GEMINI_TTS_DEFAULT_VOICE
)
GEMINI_TTS_DEFAULT_MALE_VOICE = (
    _CFG.get("default_male_voice") or FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE
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


def _create_provider() -> NativeVoiceProvider:
    """Always succeed — provider 必须留在 registry 里，否则下游 routing 会
    把内置 Gemini 音色误判为 custom。catalog/默认值上面已经走过 config →
    fallback 的 OR 链，到这里保证非空。"""
    aliases_source = _CFG.get("aliases") or _FALLBACK_GEMINI_TTS_VOICE_ALIASES
    return NativeVoiceProvider(
        key="gemini",
        catalog=GEMINI_TTS_VOICE_GENDERS,
        aliases=_build_aliases(aliases_source),
        default_voice=GEMINI_TTS_DEFAULT_VOICE,
        default_male_voice=GEMINI_TTS_DEFAULT_MALE_VOICE,
        catalog_prefix=_CFG.get("catalog_prefix") or "Gemini",
        catalog_value_is_display_name=bool(
            _CFG.get("catalog_value_is_display_name", False)
        ),
    )


GEMINI_PROVIDER = _create_provider()
register_provider(GEMINI_PROVIDER)


def normalize_gemini_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Wire-format helper for Gemini-bound code paths (gemini_tts_worker,
    omni_realtime_client). Cross-cutting code should go through the registry."""
    return GEMINI_PROVIDER.normalize(voice_id)


def is_gemini_tts_voice(voice_id: str | None) -> bool:
    return GEMINI_PROVIDER.is_voice(voice_id)
