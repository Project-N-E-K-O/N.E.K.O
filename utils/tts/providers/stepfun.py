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

"""StepFun native TTS voice catalog registration.

Voice IDs, display names and defaults are read from the native_tts_voice_providers
field of config/api_providers.json, avoiding hardcoded upstream voice_ids in business code.

Official voice reference:
https://platform.stepfun.com/docs/zh/guides/developer/tts
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.tts.native_voice_registry import (
    NativeVoiceProvider,
    get_provider,
    register_provider,
)

FALLBACK_STEPFUN_TTS_DEFAULT_VOICE = "linjiameimei"
FALLBACK_STEPFUN_TTS_DEFAULT_MALE_VOICE = "cixingnansheng"


def _load_stepfun_provider_config(provider_key: str) -> dict:
    """Read and normalize the StepFun voice provider config from api_providers.json."""
    return get_native_tts_voice_provider_config(provider_key)


def _build_aliases(catalog: dict[str, str], configured_aliases: dict[str, str]) -> dict[str, str]:
    """Merge display-name aliases with configured aliases."""
    aliases = {
        label.casefold(): voice_id
        for voice_id, label in catalog.items()
        if label
    }
    aliases.update({
        alias.casefold(): voice_id
        for alias, voice_id in configured_aliases.items()
        if alias and voice_id
    })
    return aliases


def _create_provider(provider_key: str) -> NativeVoiceProvider | None:
    """Create the NativeVoiceProvider from config; skip registration when config is missing."""
    cfg = _load_stepfun_provider_config(provider_key)
    catalog = cfg.get('voices') or {}
    default_voice = cfg.get('default_voice') or ''
    default_male_voice = cfg.get('default_male_voice') or default_voice
    if not catalog or not default_voice:
        return None
    return NativeVoiceProvider(
        key=provider_key,
        catalog=catalog,
        aliases=_build_aliases(catalog, cfg.get('aliases') or {}),
        default_voice=default_voice,
        default_male_voice=default_male_voice,
        catalog_prefix=cfg.get('catalog_prefix') or provider_key,
        catalog_value_is_display_name=bool(cfg.get('catalog_value_is_display_name', False)),
    )


_STEP_CONFIG = _load_stepfun_provider_config("step")
STEPFUN_TTS_VOICE_LABELS: dict[str, str] = _STEP_CONFIG.get('voices') or {}
STEPFUN_TTS_DEFAULT_VOICE = _STEP_CONFIG.get('default_voice') or FALLBACK_STEPFUN_TTS_DEFAULT_VOICE
STEPFUN_TTS_DEFAULT_MALE_VOICE = (
    _STEP_CONFIG.get('default_male_voice')
    or FALLBACK_STEPFUN_TTS_DEFAULT_MALE_VOICE
    or STEPFUN_TTS_DEFAULT_VOICE
)

STEPFUN_PROVIDER = _create_provider("step")
FREE_STEPFUN_PROVIDER = _create_provider("free")

if STEPFUN_PROVIDER is not None:
    register_provider(STEPFUN_PROVIDER)
if FREE_STEPFUN_PROVIDER is not None:
    register_provider(FREE_STEPFUN_PROVIDER)


def get_stepfun_tts_default_voice(provider_key: str = "step") -> str:
    """Read the default voice per the current StepFun route provider."""
    provider = get_provider(provider_key if provider_key in ("step", "free") else "step")
    if provider is not None and provider.default_voice:
        return provider.default_voice
    return STEPFUN_TTS_DEFAULT_VOICE


def normalize_stepfun_tts_voice(
    voice_id: str | None,
    provider_key: str = "step",
) -> tuple[str, bool]:
    """voice_id normalization helper used internally by StepFun routes."""
    provider = get_provider(provider_key if provider_key in ("step", "free") else "step")
    if provider is None:
        return (voice_id or "").strip(), False
    return provider.normalize(voice_id)


def is_stepfun_tts_voice(voice_id: str | None, provider_key: str = "step") -> bool:
    provider = get_provider(provider_key if provider_key in ("step", "free") else "step")
    if provider is None:
        return False
    return provider.is_voice(voice_id)
