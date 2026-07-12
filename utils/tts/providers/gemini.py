# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Gemini TTS adapter: catalog metadata + thin wrappers for wire-format paths.

The cross-cutting decision logic (catalog membership, routing, UI catalog,
realtime active-provider lookup, worker dispatch) lives in
`utils.tts.native_voice_registry`. This module just wires Gemini into that
registry and keeps a couple of short aliases for code that's already
Gemini-bound by virtue of speaking Gemini's wire format (the
`gemini_tts_worker` HTTP call and the Gemini Live `speech_config` setup).

Voice IDs, display genders and defaults are read preferentially from
native_tts_voice_providers.gemini in config/api_providers.json, so changing the
voice catalog doesn't require touching Python code. The fallback constants are a
copy of the pre-PR #1290 hardcoded catalog, used only when JSON loading fails —
even then the provider must stay in the registry, otherwise
`resolve_native_voice_for_routing("gemini", ...)` decides native=False,
`core._has_custom_tts()` treats built-in voices as custom, and even Puck/Leda
get routed to cosyvoice_vc_tts_worker — a routing regression sneakier than
"lost catalog metadata".

Voice list reference: https://ai.google.dev/gemini-api/docs/speech-generation
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.tts.native_voice_registry import (
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
    """Casefold alias keys so NativeVoiceProvider.normalize's casefold lookup can hit them.
    Difference from stepfun_tts_voices._build_aliases: Gemini's catalog values are
    genders (Female/Male) rather than display names, and must not be injected back as aliases."""
    return {
        alias.casefold(): voice_id
        for alias, voice_id in configured.items()
        if alias and voice_id
    }


def _resolved_alias_fallback(
    template: dict[str, str],
    default_voice: str,
    default_male_voice: str,
) -> dict[str, str]:
    """Point fallback gender aliases at the effective configured defaults."""
    return {
        alias: (
            default_male_voice
            if voice_id == FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE
            else default_voice
        )
        for alias, voice_id in template.items()
    }


def _create_provider() -> NativeVoiceProvider:
    """Always succeed — the provider must stay in the registry, otherwise downstream
    routing misclassifies built-in Gemini voices as custom. The catalog/defaults have
    already gone through the config → fallback OR chain above and are guaranteed
    non-empty here."""
    aliases_source = _CFG.get("aliases") or _resolved_alias_fallback(
        _FALLBACK_GEMINI_TTS_VOICE_ALIASES,
        GEMINI_TTS_DEFAULT_VOICE,
        GEMINI_TTS_DEFAULT_MALE_VOICE,
    )
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
    """Same non-empty registration guarantee as _create_provider: when config is missing,
    fall back to the Gemini catalog plus yui, so a missing free_intl doesn't cause
    yui/Gemini voices on the overseas free route to be treated as custom and misrouted
    to external TTS."""
    cfg = get_native_tts_voice_provider_config("free_intl")
    default_voice = cfg.get("default_voice") or "yui"
    default_male_voice = (
        cfg.get("default_male_voice") or FALLBACK_GEMINI_TTS_DEFAULT_MALE_VOICE
    )
    aliases_source = cfg.get("aliases") or _resolved_alias_fallback(
        _FALLBACK_FREE_INTL_VOICE_ALIASES,
        default_voice,
        default_male_voice,
    )
    return NativeVoiceProvider(
        key="free_intl",
        catalog=cfg.get("voices") or _FALLBACK_FREE_INTL_VOICE_GENDERS,
        aliases=_build_aliases(aliases_source),
        default_voice=default_voice,
        default_male_voice=default_male_voice,
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
