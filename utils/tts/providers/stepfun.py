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

_FALLBACK_STEPFUN_TTS_VOICE_LABELS: dict[str, str] = {
    "elegantgentle-female": "高雅女声",
    "livelybreezy-female": "活力女声",
    "qingchunshaonv": "青春少女",
    "wenrounansheng": "温柔男声",
    "linjiameimei": "邻家妹妹",
    "cixingnansheng": "磁性男声",
}


def _fallback_aliases(default_voice: str, default_male_voice: str) -> dict[str, str]:
    return {
        "default": default_voice,
        "默认": default_voice,
        "female": default_voice,
        "woman": default_voice,
        "女": default_voice,
        "女声": default_voice,
        "中文女": default_voice,
        "male": default_male_voice,
        "man": default_male_voice,
        "男": default_male_voice,
        "男声": default_male_voice,
        "中文男": default_male_voice,
    }


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


def _create_provider(provider_key: str) -> NativeVoiceProvider:
    """Create a provider from config, falling back to a routable built-in catalog."""
    cfg = _load_stepfun_provider_config(provider_key)
    configured_catalog = cfg.get('voices')
    configured_default = cfg.get('default_voice')
    config_is_routable = bool(
        isinstance(configured_catalog, dict)
        and configured_catalog
        and isinstance(configured_default, str)
        and configured_default in configured_catalog
    )
    effective_cfg = cfg if config_is_routable else {}
    catalog = (
        configured_catalog
        if config_is_routable
        else _FALLBACK_STEPFUN_TTS_VOICE_LABELS
    )
    default_voice = (
        configured_default
        if config_is_routable
        else FALLBACK_STEPFUN_TTS_DEFAULT_VOICE
    )
    configured_male = effective_cfg.get('default_male_voice')
    default_male_voice = (
        configured_male
        if isinstance(configured_male, str) and configured_male in catalog
        else (
            default_voice
            if config_is_routable
            else FALLBACK_STEPFUN_TTS_DEFAULT_MALE_VOICE
        )
    )
    configured_aliases = effective_cfg.get('aliases')
    aliases = (
        configured_aliases
        if isinstance(configured_aliases, dict) and configured_aliases
        else _fallback_aliases(default_voice, default_male_voice)
    )
    return NativeVoiceProvider(
        key=provider_key,
        catalog=catalog,
        aliases=_build_aliases(catalog, aliases),
        default_voice=default_voice,
        default_male_voice=default_male_voice,
        catalog_prefix=(
            effective_cfg.get('catalog_prefix')
            or ("免费 API" if provider_key == "free" else "StepFun")
        ),
        catalog_value_is_display_name=bool(
            effective_cfg.get('catalog_value_is_display_name', True)
        ),
    )


_STEP_CONFIG = _load_stepfun_provider_config("step")
STEPFUN_PROVIDER = _create_provider("step")
FREE_STEPFUN_PROVIDER = _create_provider("free")

STEPFUN_TTS_VOICE_LABELS: dict[str, str] = dict(STEPFUN_PROVIDER.catalog)
STEPFUN_TTS_DEFAULT_VOICE = STEPFUN_PROVIDER.default_voice
STEPFUN_TTS_DEFAULT_MALE_VOICE = STEPFUN_PROVIDER.default_male_voice

register_provider(STEPFUN_PROVIDER)
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
