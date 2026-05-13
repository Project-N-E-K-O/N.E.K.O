"""xAI Grok TTS adapter: catalog metadata for grok streaming TTS voices.

Mirrors `utils.gemini_tts_voices` — cross-cutting decision logic lives in
`utils.native_voice_registry`; this module just wires Grok's 5 built-in voices
into the registry so `core._has_custom_tts()` correctly classifies them as
native (not custom), and `get_tts_worker` dispatches to
`grok_streaming_tts_worker` instead of falling through to `cosyvoice_vc_tts_worker`.

音色 ID、性别标签和默认值优先读取自 config/api_providers.json 的
native_tts_voice_providers.grok。fallback 常量是 PR #1336 之前的硬编码目录的
副本，仅在 JSON 加载失败时兜底——此时 provider 仍必须留在 registry 里，
否则 `is_native_voice("leo", "grok")` 返 False，`core._has_custom_tts()` 把
eve/leo 之类内置音色当 custom，最终 `get_tts_worker` 路由到
`cosyvoice_vc_tts_worker` 而非 `grok_streaming_tts_worker`，比"丢失目录"
更隐蔽的 routing 回归。

Voice list reference: xAI `GET /v1/tts/voices` (eve / ara / leo / rex / sal).
The upstream API expects lowercase voice ids; we mirror that in the catalog.
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.native_voice_registry import (
    NativeVoiceProvider,
    register_provider,
)

FALLBACK_GROK_TTS_DEFAULT_VOICE = "eve"
FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE = "leo"

# 与 api_providers.json 的 native_tts_voice_providers.grok.voices 保持同形；
# config 是权威源，这份是 JSON 加载失败时的兜底，保证 provider 始终注册。
# Gender 标签是 best-effort 推断（xAI 文档只列 voice_id + name + language），
# 仅用于 UI 展示，routing/dispatch 只看 key。
_FALLBACK_GROK_TTS_VOICE_GENDERS: dict[str, str] = {
    "eve": "Female",
    "ara": "Female",
    "leo": "Male",
    "rex": "Male",
    "sal": "Male",
}

_FALLBACK_GROK_TTS_VOICE_ALIASES: dict[str, str] = {
    "male": FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE,
    "man": FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE,
    "男": FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE,
    "男声": FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE,
    "female": FALLBACK_GROK_TTS_DEFAULT_VOICE,
    "woman": FALLBACK_GROK_TTS_DEFAULT_VOICE,
    "女": FALLBACK_GROK_TTS_DEFAULT_VOICE,
    "女声": FALLBACK_GROK_TTS_DEFAULT_VOICE,
}


def _load_provider_config() -> dict:
    return get_native_tts_voice_provider_config("grok")


_CFG = _load_provider_config()

GROK_TTS_VOICE_GENDERS: dict[str, str] = (
    _CFG.get("voices") or _FALLBACK_GROK_TTS_VOICE_GENDERS
)
GROK_TTS_DEFAULT_VOICE = (
    _CFG.get("default_voice") or FALLBACK_GROK_TTS_DEFAULT_VOICE
)
GROK_TTS_DEFAULT_MALE_VOICE = (
    _CFG.get("default_male_voice") or FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE
)


def _build_aliases(configured: dict[str, str]) -> dict[str, str]:
    """同 gemini_tts_voices：只 casefold configured aliases，不把 catalog 的
    Female/Male 标签当 alias 注入。"""
    return {
        alias.casefold(): voice_id
        for alias, voice_id in configured.items()
        if alias and voice_id
    }


def _create_provider() -> NativeVoiceProvider:
    """Always succeed — provider 必须留在 registry，否则下游 routing 会
    把 eve/leo 这种内置 voice 当 custom 走 cosyvoice。"""
    aliases_source = _CFG.get("aliases") or _FALLBACK_GROK_TTS_VOICE_ALIASES
    return NativeVoiceProvider(
        key="grok",
        catalog=GROK_TTS_VOICE_GENDERS,
        aliases=_build_aliases(aliases_source),
        default_voice=GROK_TTS_DEFAULT_VOICE,
        default_male_voice=GROK_TTS_DEFAULT_MALE_VOICE,
        catalog_prefix=_CFG.get("catalog_prefix") or "Grok",
        catalog_value_is_display_name=bool(
            _CFG.get("catalog_value_is_display_name", False)
        ),
    )


GROK_PROVIDER = _create_provider()
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
