"""阶跃星辰 TTS 原生音色目录注册。

音色 ID、展示名和默认值读取自 config/api_providers.json 的
native_tts_voice_providers 字段，避免把上游 voice_id 写死在业务代码里。

官方音色参考：
https://platform.stepfun.com/docs/zh/guides/developer/tts
"""

from utils.api_config_loader import get_native_tts_voice_provider_config
from utils.native_voice_registry import (
    NativeVoiceProvider,
    get_provider,
    register_provider,
)

FALLBACK_STEPFUN_TTS_DEFAULT_VOICE = "qingchunshaonv"
FALLBACK_STEPFUN_TTS_DEFAULT_MALE_VOICE = "cixingnansheng"


def _load_stepfun_provider_config(provider_key: str) -> dict:
    """从 api_providers.json 读取并规范化阶跃音色 Provider 配置。"""
    return get_native_tts_voice_provider_config(provider_key)


def _build_aliases(catalog: dict[str, str], configured_aliases: dict[str, str]) -> dict[str, str]:
    """合并展示名别名与配置别名。"""
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
    """根据配置创建 NativeVoiceProvider，配置缺失时跳过注册。"""
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
    """按当前阶跃线路 Provider 读取默认音色。"""
    provider = get_provider(provider_key if provider_key in ("step", "free") else "step")
    if provider is not None and provider.default_voice:
        return provider.default_voice
    return STEPFUN_TTS_DEFAULT_VOICE


def normalize_stepfun_tts_voice(
    voice_id: str | None,
    provider_key: str = "step",
) -> tuple[str, bool]:
    """阶跃线路内部使用的 voice_id 规范化辅助函数。"""
    provider = get_provider(provider_key if provider_key in ("step", "free") else "step")
    if provider is None:
        return (voice_id or "").strip(), False
    return provider.normalize(voice_id)


def is_stepfun_tts_voice(voice_id: str | None, provider_key: str = "step") -> bool:
    provider = get_provider(provider_key if provider_key in ("step", "free") else "step")
    if provider is None:
        return False
    return provider.is_voice(voice_id)
