"""xAI Grok TTS adapter: catalog metadata for grok streaming TTS voices.

Mirrors `utils.gemini_tts_voices` — cross-cutting decision logic lives in
`utils.native_voice_registry`; this module just wires Grok's 5 built-in voices
into the registry so `core._has_custom_tts()` correctly classifies them as
native (not custom), and `get_tts_worker` dispatches to
`grok_streaming_tts_worker` instead of falling through to `cosyvoice_vc_tts_worker`.

音色 ID、性别标签和默认值读取自 config/api_providers.json 的
native_tts_voice_providers.grok，避免把 voice 列表写死在 Python 里。
配置缺失时 GROK_PROVIDER=None，调用方会回退到 strip(voice_id) 后透传。

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


def _load_provider_config() -> dict:
    return get_native_tts_voice_provider_config("grok")


_CFG = _load_provider_config()

# Gender labels are best-effort inferences from the canonical given-name
# associations — xAI's docs only list voice_id + name + language, not gender.
# The labels feed the UI display only; routing/dispatch only consult the keys.
GROK_TTS_VOICE_GENDERS: dict[str, str] = _CFG.get("voices") or {}
GROK_TTS_DEFAULT_VOICE = _CFG.get("default_voice") or FALLBACK_GROK_TTS_DEFAULT_VOICE
GROK_TTS_DEFAULT_MALE_VOICE = (
    _CFG.get("default_male_voice")
    or FALLBACK_GROK_TTS_DEFAULT_MALE_VOICE
    or GROK_TTS_DEFAULT_VOICE
)


def _build_aliases(configured: dict[str, str]) -> dict[str, str]:
    """同 gemini_tts_voices：只 casefold configured aliases，不把 catalog 的
    Female/Male 标签当 alias 注入。"""
    return {
        alias.casefold(): voice_id
        for alias, voice_id in configured.items()
        if alias and voice_id
    }


def _create_provider() -> NativeVoiceProvider | None:
    if not GROK_TTS_VOICE_GENDERS or not GROK_TTS_DEFAULT_VOICE:
        return None
    return NativeVoiceProvider(
        key="grok",
        catalog=GROK_TTS_VOICE_GENDERS,
        aliases=_build_aliases(_CFG.get("aliases") or {}),
        default_voice=GROK_TTS_DEFAULT_VOICE,
        default_male_voice=GROK_TTS_DEFAULT_MALE_VOICE,
        catalog_prefix=_CFG.get("catalog_prefix") or "Grok",
        catalog_value_is_display_name=bool(
            _CFG.get("catalog_value_is_display_name", False)
        ),
    )


GROK_PROVIDER = _create_provider()

if GROK_PROVIDER is not None:
    register_provider(GROK_PROVIDER)


def normalize_grok_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Wire-format helper: map any user-input voice (canonical id, alias,
    or empty) to a canonical xAI voice id.

    Mirrors `utils.gemini_tts_voices.normalize_gemini_tts_voice`. The
    streaming TTS worker calls this before building the `voice` query
    parameter, because the routing layer accepts aliases like ``male`` /
    ``女声`` (via `NativeVoiceProvider.is_voice`) but xAI's endpoint only
    accepts canonical ids (eve/ara/leo/rex/sal) or 8-char custom voice ids.

    Empty / unrecognized input always resolves to ``GROK_TTS_DEFAULT_VOICE``
    (eve by default) so grok_streaming_tts_worker never forwards an empty
    ``voice`` query param — even on the degraded code path where
    ``api_providers.json`` failed to load and `GROK_PROVIDER` is None.
    """
    if GROK_PROVIDER is None:
        normalized = (voice_id or "").strip()
        return (normalized or GROK_TTS_DEFAULT_VOICE), False
    return GROK_PROVIDER.normalize(voice_id)
