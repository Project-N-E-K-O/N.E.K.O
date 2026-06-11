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


# ---- free_intl：海外免费（free + *.lanlan.app）的有效音色目录 ----
# 海外免费路由 core_api_type 仍是 'free'（走 lanlan.app 的 Gemini 代理），
# 但其可选音色是 Gemini 全量 + 一个品牌 yui 音色（服务端识别字面量 "yui"，
# 映射到 yui 角色专属声音）。这里复用 Gemini 目录，叠加 yui，并把 "default"
# 别名指到 Leda。registry 由 native_voice_registry 按 host 把 free→free_intl
# 重映射，cross-cutting 文件无需感知。
_FALLBACK_FREE_INTL_VOICE_GENDERS: dict[str, str] = {
    "yui": "Female",
    **_FALLBACK_GEMINI_TTS_VOICE_GENDERS,
}
_FALLBACK_FREE_INTL_VOICE_ALIASES: dict[str, str] = {
    **_FALLBACK_GEMINI_TTS_VOICE_ALIASES,
    "default": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
    "默认": FALLBACK_GEMINI_TTS_DEFAULT_VOICE,
}


def _create_free_intl_provider() -> NativeVoiceProvider:
    """与 _create_provider 同样保证非空注册：缺失 config 时回退到 Gemini 目录
    叠加 yui，避免 free_intl 缺席导致 yui/Gemini 音色在海外免费路由被当 custom
    误路由到外部 TTS。"""
    cfg = get_native_tts_voice_provider_config("free_intl")
    return NativeVoiceProvider(
        key="free_intl",
        catalog=cfg.get("voices") or _FALLBACK_FREE_INTL_VOICE_GENDERS,
        aliases=_build_aliases(cfg.get("aliases") or _FALLBACK_FREE_INTL_VOICE_ALIASES),
        default_voice=cfg.get("default_voice") or "yui",
        default_male_voice=(
            cfg.get("default_male_voice") or FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE
        ),
        catalog_prefix=cfg.get("catalog_prefix") or "Gemini",
        catalog_value_is_display_name=bool(
            cfg.get("catalog_value_is_display_name", False)
        ),
    )


FREE_INTL_PROVIDER = _create_free_intl_provider()
register_provider(FREE_INTL_PROVIDER)


def normalize_gemini_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Wire-format helper for Gemini-bound code paths (gemini_tts_worker,
    omni_realtime_client). Cross-cutting code should go through the registry."""
    return GEMINI_PROVIDER.normalize(voice_id)


def is_gemini_tts_voice(voice_id: str | None) -> bool:
    return GEMINI_PROVIDER.is_voice(voice_id)
