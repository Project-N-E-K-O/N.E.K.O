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

"""xAI Grok TTS adapter: catalog metadata for grok streaming TTS voices.

Mirrors `utils.tts.providers.gemini` — cross-cutting decision logic lives in
`utils.tts.native_voice_registry`; this module just wires Grok's 5 built-in voices
into the registry so `core._has_custom_tts()` correctly classifies them as
native (not custom), and `get_tts_worker` dispatches to
`grok_streaming_tts_worker` instead of falling through to `cosyvoice_vc_tts_worker`.

Voice IDs, gender labels and defaults are read preferentially from
native_tts_voice_providers.grok in config/api_providers.json. The fallback
constants are a copy of the pre-PR #1336 hardcoded catalog, used only when JSON
loading fails — even then the provider must stay in the registry, otherwise
`is_native_voice("leo", "grok")` returns False, `core._has_custom_tts()` treats
built-in voices like eve/leo as custom, and `get_tts_worker` ends up routing to
`cosyvoice_vc_tts_worker` instead of `grok_streaming_tts_worker` — a routing
regression sneakier than a "lost catalog".

Voice list reference: xAI `GET /v1/tts/voices` (eve / ara / leo / rex / sal).
The upstream API expects lowercase voice ids; we mirror that in the catalog.
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.tts.native_voice_registry import (
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
    """Same as gemini_tts_voices: only casefold configured aliases; never inject the
    catalog's Female/Male labels as aliases."""
    return {
        alias.casefold(): voice_id
        for alias, voice_id in configured.items()
        if alias and voice_id
    }


def _create_provider() -> NativeVoiceProvider:
    """Always succeed — the provider must stay in the registry, otherwise downstream
    routing treats built-in voices like eve/leo as custom and routes them to cosyvoice."""
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

    Mirrors `utils.tts.providers.gemini.normalize_gemini_tts_voice`. The
    streaming TTS worker calls this before building the `voice` query
    parameter, because the routing layer accepts aliases like ``male`` /
    ``女声`` (via `NativeVoiceProvider.is_voice`) but xAI's endpoint only
    accepts canonical ids (eve/ara/leo/rex/sal) or 8-char custom voice ids.
    """
    return GROK_PROVIDER.normalize(voice_id)
